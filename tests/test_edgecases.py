# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""Edge-case tests for branches not covered by the main test suites.

Derived from a coverage audit (94% total, ~195 missed lines). Each test
targets a specific uncovered branch: parser fallbacks, factory dispatch,
orphan/effect paths, event-router guards, reload/window/parent handlers,
matcher buckets, box-model fallbacks, cache/store/suppress/writer utils.
"""

import gc
import weakref
from collections.abc import Callable

import pytest
from pytestqt.qtbot import QtBot

import qt_css_engine
from qt_css_engine import TransitionEngine
from qt_css_engine.animation.base import StepsReversalMixin
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.factory import Animation, create_animator
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.css.gradients import (
    _fill_positions,
    _parse_pos_value,
    _split_args,
    translate_gradients,
)
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause, ResolvedRuleState
from qt_css_engine.engine.evaluator import Evaluation
from qt_css_engine.geometry.box_model import (
    content_box_px,
    margin_side_px,
    padding_side_px,
    total_border_px,
)
from qt_css_engine.geometry.clamp import clamp_border_radius
from qt_css_engine.matching.cache import IdentityCache, WidgetCache
from qt_css_engine.matching.compiler import CompiledSegment, WidgetIdentity, compile_segment
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat import is_qobject_alive, qt_delete
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QEvent, Qt
from qt_css_engine.qt_compat.QtGui import QColor, QMouseEvent
from qt_css_engine.qt_compat.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QPushButton,
    QWidget,
)
from qt_css_engine.state.pseudo import PseudoMachine
from qt_css_engine.state.store import WidgetStore
from qt_css_engine.state.suppress import InternalWriteReason, is_suppressed, suppress
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.writer import StyleWriter, scoped_anim_style
from qt_css_engine.utils.color import parse_box_shadow, parse_color
from qt_css_engine.utils.easing import make_cubic_bezier_curve, make_steps_curve, resolve_easing_curve
from qt_css_engine.utils.parsing import parse_css_numeric, parse_css_val
from qt_css_engine.utils.qt_helpers import safe_disconnect


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


def hover_widget(engine: TransitionEngine, widget: QWidget) -> None:
    engine.get_context(widget).active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)


def _anims(engine: TransitionEngine, widget: QWidget) -> dict[str, Animation]:
    ctx = engine.store.contexts.get(id(widget))
    return ctx.active_animations if ctx is not None else {}


# ---------------------------------------------------------------------------
# package __getattr__
# ---------------------------------------------------------------------------


def test_package_getattr_invalid_raises() -> None:
    with pytest.raises(AttributeError):
        getattr(qt_css_engine, "NoSuchThing")


def test_package_getattr_extract_rules_lazy() -> None:
    fn = getattr(qt_css_engine, "extract_rules")
    assert callable(fn)


# ---------------------------------------------------------------------------
# animation base / factory
# ---------------------------------------------------------------------------


def test_is_steps_curve_with_type_returns_false() -> None:
    assert StepsReversalMixin.is_steps_curve(QEasingCurve.Type.Linear) is False  # type: ignore[arg-type]
    assert StepsReversalMixin.is_steps_curve("ease") is False  # type: ignore[arg-type]


def test_factory_returns_none_for_unsupported_prop(_app: QApplication) -> None:
    widget = QWidget()
    try:
        assert create_animator(widget, "display", "block", 200, QEasingCurve.Type.Linear) is None
        assert create_animator(widget, "z-index", "5", 200, QEasingCurve.Type.Linear) is None
    finally:
        destroy(widget)


def test_factory_numeric_invalid_initial_returns_none(_app: QApplication) -> None:
    widget = QWidget()
    try:
        assert create_animator(widget, "width", "auto", 200, QEasingCurve.Type.Linear) is None
    finally:
        destroy(widget)


def test_color_animation_without_ctx_fallback_style(_app: QApplication) -> None:
    """No ctx + no flush callback → direct setStyleSheet fallback (color.py:60)."""
    widget = QWidget()
    try:
        anim = ColorAnimation(widget, "background-color", "red", 200, QEasingCurve.Type.Linear)
        anim.set_target("blue")
        assert "background-color" in widget.styleSheet() or anim.current_color.isValid()
        anim.anim.stop()
    finally:
        destroy(widget)


def test_numeric_on_tick_none_stops(_app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetState()
    try:
        anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=ctx)
        anim.set_target("50px")
        anim._on_tick(None)  # type: ignore[arg-type]
        assert anim.anim.state() != anim.anim.State.Running
    finally:
        destroy(widget)


def test_numeric_set_target_invalid_string_noop(_app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetState()
    try:
        anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=ctx)
        anim.set_target("not-a-length!!!")
        assert anim.anim.state() != anim.anim.State.Running
    finally:
        destroy(widget)


def test_opacity_set_target_invalid_noop(_app: QApplication) -> None:
    widget = QWidget()
    try:
        anim = OpacityAnimation(widget, 1.0, 200, QEasingCurve.Type.Linear)
        anim.set_target("auto")
        assert anim.anim.state() != anim.anim.State.Running
        anim.snap_to("auto")  # invalid snap → keeps current value
        assert anim._current_val == pytest.approx(1.0)
    finally:
        destroy(widget)


def test_shadow_tick_with_no_endpoints_noop(_app: QApplication) -> None:
    widget = QWidget()
    try:
        handle = BoxShadowHandle(widget, "none", 200, QEasingCurve.Type.Linear)
        assert handle._current is None
        handle._on_tick(0.5)  # both _start/_end None → early return
        assert handle._current is None
        assert handle.anim.state() != handle.anim.State.Running
    finally:
        destroy(widget)


def test_shadow_set_target_same_noop(_app: QApplication) -> None:
    widget = QWidget()
    try:
        # Not running + same as current → no-op
        h1 = BoxShadowHandle(widget, "none", 200, QEasingCurve.Type.Linear)
        h1.set_target("none")
        assert h1.anim.state() != h1.anim.State.Running
        # Running + same as _end → no-op
        h2 = BoxShadowHandle(widget, "none", 200, QEasingCurve.Type.Linear)
        h2.set_target("2px 2px 4px black")
        assert h2.anim.state() == h2.anim.State.Running
        end_before = h2._end
        h2.set_target("2px 2px 4px black")
        assert h2._end == end_before
        h2.anim.stop()
    finally:
        destroy(widget)


def test_generic_snap_to_natural_removes_prop(_app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetState()
    try:
        anim = GenericPropertyAnimation(widget, "width", 100.0, 200, QEasingCurve.Type.Linear, ctx=ctx)
        ctx.css_anim_props["width"] = "100.000px"
        anim.snap_to_natural()
        assert "width" not in ctx.css_anim_props
        assert anim.anim.state() != anim.anim.State.Running
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# gradients
# ---------------------------------------------------------------------------


def test_parse_pos_value_invalid_returns_default() -> None:
    assert _parse_pos_value("foo") == pytest.approx(0.5)
    assert _parse_pos_value("") == pytest.approx(0.5)
    assert _parse_pos_value("50%") == pytest.approx(0.5)
    assert _parse_pos_value("0.25") == pytest.approx(0.25)


def test_radial_conic_empty_inner_passthrough() -> None:
    assert translate_gradients("radial-gradient()") == "radial-gradient()"
    assert translate_gradients("conic-gradient()") == "conic-gradient()"
    assert translate_gradients("linear-gradient()") == "linear-gradient()"


def test_fill_positions_multi_gap_interpolated() -> None:
    raw: list[tuple[float | None, str]] = [(0.0, "a"), (None, "b"), (None, "c"), (1.0, "d")]
    out = _fill_positions(raw)
    assert out[1][0] == pytest.approx(1 / 3)
    assert out[2][0] == pytest.approx(2 / 3)


def test_split_args_empty_string() -> None:
    assert _split_args("") == []


# ---------------------------------------------------------------------------
# parser fallbacks
# ---------------------------------------------------------------------------


def test_transition_property_non_ident_skipped() -> None:
    _, rules = extract_rules(".box { transition-property: 123; transition-duration: 200ms; }")
    assert rules[0].transitions == []


def test_transition_duration_invalid_token_skipped() -> None:
    _, rules = extract_rules(".box { transition-property: color; transition-duration: solid; }")
    assert rules[0].transitions == []


def test_transition_easing_unknown_function_defaults_to_ease() -> None:
    _, rules = extract_rules(
        ".box { transition-property: color; transition-duration: 200ms; transition-timing-function: foo(1); }"
    )
    assert len(rules[0].transitions) == 1
    assert rules[0].transitions[0].easing == "ease"


def test_transition_segment_too_short_ignored() -> None:
    _, rules = extract_rules(".box { transition: color; }")
    assert rules[0].transitions == []


def test_transition_segment_non_ident_prop_ignored() -> None:
    _, rules = extract_rules(".box { transition: 123 200ms; }")
    assert rules[0].transitions == []


def test_transition_segment_bad_duration_ignored() -> None:
    _, rules = extract_rules(".box { transition: color solid; }")
    assert rules[0].transitions == []


def test_at_rule_ignored() -> None:
    cleaned, rules = extract_rules("@media screen { .box { color: red; } }")
    assert rules == []
    assert cleaned == ""


def test_empty_stylesheet_returns_empty() -> None:
    cleaned, rules = extract_rules("")
    assert rules == []
    assert cleaned == ""


def test_transition_property_zero_duration_number() -> None:
    _, rules = extract_rules(".box { transition-property: opacity; transition-duration: 0; }")
    assert rules[0].transitions[0].duration_ms == 0


# ---------------------------------------------------------------------------
# cascade
# ---------------------------------------------------------------------------


def test_expand_all_with_no_spec_still_marks_animatable(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        state = ResolvedRuleState(base_props={"color": "red"}, target_props={"color": "blue"})
        state.animated_props.add("all")  # no "all" in transitions → all_spec None
        engine.cascade.expand_all(ctx, state)
        assert "all" not in state.animated_props
        assert "color" in state.animated_props
        assert "color" not in state.transitions
    finally:
        destroy(widget)


def test_collect_border_radius_non_px_and_invalid_ignored(_app: QApplication) -> None:
    engine = make_engine(".box { border-radius: 4px; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(100, 100)
    try:
        ctx = engine.get_context(widget)
        state = ResolvedRuleState(target_props={"border-top-left-radius": "50%"})
        engine.cascade.collect_border_radius(widget, ctx, state)
        assert "border-top-left-radius" not in state.animated_props
        state2 = ResolvedRuleState(target_props={"border-top-left-radius": "auto"})
        engine.cascade.collect_border_radius(widget, ctx, state2)
        assert "border-top-left-radius" not in state2.animated_props
    finally:
        destroy(widget)


def test_needs_qt_clamp_guards() -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    try:
        assert engine.cascade.needs_qt_border_radius_clamp(widget, {}, "color") is False
        assert engine.cascade.needs_qt_border_radius_clamp(widget, {}, "border-top-left-radius") is False
        assert (
            engine.cascade.needs_qt_border_radius_clamp(
                widget, {"border-top-left-radius": "50%"}, "border-top-left-radius"
            )
            is False
        )
    finally:
        destroy(widget)


def test_is_animatable_matrix() -> None:
    assert engine_is_animatable("color") is True
    assert engine_is_animatable("background-color") is True
    assert engine_is_animatable("opacity") is True
    assert engine_is_animatable("box-shadow") is True
    assert engine_is_animatable("width") is True
    assert engine_is_animatable("display") is False


def engine_is_animatable(prop: str) -> bool:
    from qt_css_engine.css.properties import is_animatable

    return is_animatable(prop)


# ---------------------------------------------------------------------------
# evaluator — skip / resolve / snap paths
# ---------------------------------------------------------------------------


def test_can_skip_initial_polish_with_no_animated_rules(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        assert engine.evaluator.can_skip_initial_evaluation(widget, ctx, EvaluationCause.POLISH) is True
        # Non-polish causes never skip
        assert engine.evaluator.can_skip_initial_evaluation(widget, ctx, EvaluationCause.DIRECT) is False
        # Existing anim state prevents skip
        ctx.css_anim_props["color"] = "red"
        assert engine.evaluator.can_skip_initial_evaluation(widget, ctx, EvaluationCause.POLISH) is False
    finally:
        destroy(widget)


def test_evaluate_skips_when_no_rule_needs_engine(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        ctx = engine.get_context(widget)
        assert not ctx.active_animations
        assert ctx.css_anim_props == {}
    finally:
        destroy(widget)


def test_fire_delayed_prop_not_should_evaluate_noop(_app: QApplication) -> None:
    engine = make_engine(".box:hover { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "unrelated")
    try:
        engine.evaluator.fire_delayed_prop(widget, "background-color")  # should early-return, no crash
    finally:
        destroy(widget)


def test_fire_delayed_prop_flushes_on_snap(_app: QApplication) -> None:
    """Delayed prop that snaps (animations disabled at fire time) must flush inline style."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        ctx = engine.get_context(widget)
        assert "background-color" in ctx.pending_delays
        engine.animations_enabled = False
        for t in list(ctx.pending_delays.values()):
            t.stop()
        engine.evaluator.fire_delayed_prop(widget, "background-color")
        assert "background-color" in ctx.css_anim_props
    finally:
        engine.animations_enabled = True
        destroy(widget)


def test_apply_prop_resolved_none_noop(_app: QApplication) -> None:
    engine = make_engine(".x { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "x")
    try:
        ctx = engine.get_context(widget)
        ev = Evaluation(widget, ctx, ResolvedRuleState(), EvaluationCause.PSEUDO_STATE)
        assert engine.evaluator.apply_prop(ev, "font-weight") is False
    finally:
        destroy(widget)


def test_resolve_no_base_value_starts_from_target(_app: QApplication) -> None:
    engine = make_engine(".x { color: red; }")
    widget = QWidget()
    try:
        ctx = engine.get_context(widget)
        ev = Evaluation(widget, ctx, ResolvedRuleState(target_props={"color": "blue"}), EvaluationCause.PSEUDO_STATE)
        resolved = engine.evaluator.resolve_property(ev, "color")
        assert resolved is not None
        assert resolved.current == resolved.target == "blue"
    finally:
        destroy(widget)


def test_prepare_animation_none_for_unsupported_prop(_app: QApplication) -> None:
    engine = make_engine("""
        .box { transition: display 200ms; }
        .box:hover { display: block; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        assert "display" not in _anims(engine, widget)
    finally:
        destroy(widget)


def test_replace_finished_callback_replaces_old(_app: QApplication) -> None:
    engine = make_engine(".box { background-color: red; transition: background-color 300ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        anim_obj = _anims(engine, widget)["background-color"]
        calls: list[str] = []
        empty_cbs: dict[str, Callable[[], None]] = {}
        engine.evaluator._replace_finished_callback(anim_obj, "background-color", empty_cbs, lambda: calls.append("a"))
        # Replace: old must be disconnected, only new fires
        cbs: dict[str, Callable[[], None]] = {}
        engine.evaluator._replace_finished_callback(anim_obj, "background-color", cbs, lambda: calls.append("b"))
        # Simulate by invoking stored callback
        assert "background-color" in cbs
        cbs["background-color"]()
        assert calls == ["b"]
        anim_obj.anim.stop()
    finally:
        destroy(widget)


def test_orphan_with_class_callback_disconnects(_app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; transition: background-color 300ms; }
        .box:hover { background-color: royalblue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        ctx = engine.get_context(widget)
        assert "background-color" in ctx.active_animations
        # Wire a class callback so orphan removal exercises the disconnect branch
        anim_obj = ctx.active_animations["background-color"]
        engine.evaluator._wire_class_callback(widget, ctx, "background-color", anim_obj)
        assert "background-color" in ctx.class_anim_callbacks
        needs = engine.evaluator.cleanup_orphans(ctx, ResolvedRuleState())
        assert "background-color" not in ctx.active_animations
        assert "background-color" not in ctx.class_anim_callbacks
        assert needs is True
    finally:
        destroy(widget)


def test_orphan_effect_no_base_clears_graphics_effect(_app: QApplication) -> None:
    engine = make_engine(".box { opacity: 0.4; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        ctx = engine.get_context(widget)
        assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)
        needs = engine.evaluator.cleanup_orphans(ctx, ResolvedRuleState())
        assert "opacity" not in ctx.active_animations
        assert widget.graphicsEffect() is None
        assert needs is False  # effect orphans need no stylesheet flush
    finally:
        destroy(widget)


def test_orphan_snap_auto_resolves_to_natural(_app: QApplication) -> None:
    engine = make_engine(".x { transition: width 200ms; }")
    widget = QWidget()
    try:
        ctx = engine.get_context(widget)
        anim = GenericPropertyAnimation(widget, "width", 100.0, 200, QEasingCurve.Type.Linear, parent=engine, ctx=ctx)
        ctx.active_animations["width"] = anim
        ctx.css_anim_props["width"] = "100.000px"
        snap_target, is_natural = engine.evaluator._orphan_snap_target(
            anim, ResolvedRuleState(base_props={"width": "auto"}), "width"
        )
        assert is_natural is True
        assert snap_target is not None and snap_target.endswith("px")
    finally:
        destroy(widget)


def test_snap_uninterpolable_gradient_paths(_app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: red; transition: all 200ms; }
        .box:hover { background-color: blue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        # Gradient target with no existing anim and nothing stored → returns False, no write
        assert (
            engine.evaluator._snap_uninterpolable_color(ctx, "background-color", None, "linear-gradient(red, blue)")
            is False
        )
        assert "background-color" not in ctx.css_anim_props
        # Gradient target with stale inline value → evicts and returns True
        ctx.css_anim_props["background-color"] = "red"
        assert (
            engine.evaluator._snap_uninterpolable_color(ctx, "background-color", None, "linear-gradient(red, blue)")
            is True
        )
        assert "background-color" not in ctx.css_anim_props
        # Solid target with no anim → writes and returns True
        assert engine.evaluator._snap_uninterpolable_color(ctx, "background-color", None, "blue") is True
        assert ctx.css_anim_props["background-color"] == "blue"
    finally:
        destroy(widget)


def test_snap_to_target_effect_creates_anim_when_missing(_app: QApplication) -> None:
    engine = make_engine(".box { opacity: 0.5; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        engine.animations_enabled = False
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        assert "opacity" in ctx.active_animations
        assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)
    finally:
        engine.animations_enabled = True
        destroy(widget)


def test_snap_existing_natural_clears_constraint(_app: QApplication) -> None:
    engine = make_engine("""
        .box { width: 50px; transition: width 200ms; }
        .box:hover { width: 80px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(50, 20)
    try:
        hover_widget(engine, widget)
        ctx = engine.get_context(widget)
        anim_obj = ctx.active_animations.get("width")
        assert isinstance(anim_obj, GenericPropertyAnimation)
        engine.evaluator._snap_existing(widget, "width", anim_obj, "whatever", True, {})
        assert "width" not in ctx.css_anim_props
    finally:
        destroy(widget)


def test_clamp_target_passthroughs(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    try:
        assert engine.evaluator._clamp_target(widget, "color", "red", {}) == "red"
        assert engine.evaluator._clamp_target(widget, "border-top-left-radius", "auto", {}) == "auto"
        assert engine.evaluator._clamp_target(widget, "border-top-left-radius", "50%", {}) == "50%"
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# event router
# ---------------------------------------------------------------------------


def test_leave_non_window_does_not_deactivate(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    parent = QWidget()
    child = QWidget(parent)
    child.setProperty("class", "btn")
    try:
        hover_widget(engine, child)
        assert ":hover" in engine.get_context(child).active_pseudos
        engine.eventFilter(child, QEvent(QEvent.Type.Leave))
        assert ":hover" in engine.get_context(child).active_pseudos
    finally:
        destroy(child)
        destroy(parent)


def test_claimed_timestamp_prevents_double_pressed(_app: QApplication) -> None:
    css = """
    .target { color: red; transition: color 200ms; }
    .target:pressed { color: blue; }
    .ancestor { color: green; transition: color 200ms; }
    .ancestor:pressed { color: yellow; }
    """
    engine = make_engine(css)
    parent = QWidget()
    parent.setProperty("class", "ancestor")
    child = QWidget(parent)
    child.setProperty("class", "target")
    try:
        from qt_css_engine.qt_compat.QtCore import QPointF

        evt = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(0, 0),
            QPointF(0, 0),
            QPointF(0, 0),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
        engine.eventFilter(child, evt)
        assert ":pressed" in engine.get_context(child).active_pseudos
        # Same event object → same timestamp → ancestor must be ignored
        engine.eventFilter(parent, evt)
        parent_ctx = engine.store.contexts.get(id(parent))
        assert parent_ctx is None or ":pressed" not in parent_ctx.active_pseudos
    finally:
        destroy(child)
        destroy(parent)


def test_clicked_activation_via_event_filter(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")
    try:
        engine.eventFilter(widget, QEvent(QEvent.Type.MouseButtonPress))
        assert ":clicked" in engine.get_context(widget).active_pseudos
    finally:
        destroy(widget)


def test_double_click_sets_pressed(_app: QApplication) -> None:
    engine = make_engine(".btn:pressed { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "btn")
    try:
        engine.eventFilter(widget, QEvent(QEvent.Type.MouseButtonDblClick))
        assert ":pressed" in engine.get_context(widget).active_pseudos
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# clicked deactivate guards
# ---------------------------------------------------------------------------


def test_deactivate_clicked_guards(_app: QApplication) -> None:
    engine = make_engine(".btn:clicked { background-color: red; }")
    widget = QWidget()
    widget.setProperty("class", "btn")
    try:
        # Unknown wid → no-op
        engine.deactivate_clicked(widget, 123456789, 0)
        # Gen mismatch → no-op
        ctx = engine.get_context(widget)
        ctx.active_pseudos.add(":clicked")
        ctx.clicked_anim_gen = 5
        engine.deactivate_clicked(widget, id(widget), 999)
        assert ":clicked" in ctx.active_pseudos
        # No :clicked pseudo → no-op
        ctx.active_pseudos.discard(":clicked")
        engine.deactivate_clicked(widget, id(widget), 5)
    finally:
        destroy(widget)


def test_deactivate_clicked_deleted_widget_no_crash(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":clicked")
    wid, gen = id(widget), ctx.clicked_anim_gen
    destroy(widget)
    engine.deactivate_clicked(widget, wid, gen)  # ctx gone → early return, no crash


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def test_destroy_untracked_widget_no_crash(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    try:
        # Never touched engine → no context
        assert id(widget) not in engine.store.contexts
        engine._on_widget_destroyed(widget)
    finally:
        destroy(widget)


def test_disconnect_deleted_anim_tolerated(_app: QApplication) -> None:
    from qt_css_engine.engine.handlers import lifecycle as lifecycle_handler

    engine = make_engine(".box { background-color: red; transition: background-color 300ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    ctx = engine.get_context(widget)
    anim_obj = ctx.active_animations["background-color"]
    cb = lambda: None
    anim_obj.anim.finished.connect(cb)
    ctx.class_anim_callbacks["background-color"] = cb
    qt_delete(anim_obj.anim)
    lifecycle_handler.disconnect_finished_callbacks(ctx, ctx.class_anim_callbacks)  # must not raise
    assert ctx.class_anim_callbacks == {}
    destroy(widget)


# ---------------------------------------------------------------------------
# parent change
# ---------------------------------------------------------------------------


def test_parent_change_no_descendant_queues_only_matching(_app: QApplication) -> None:
    engine = make_engine(".box { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    other = QWidget()
    other.setProperty("class", "other")
    try:
        assert engine.matcher.index.flags.has_descendant is False
        engine.polish.queue.clear()
        engine.polish.pending = False
        engine.on_parent_change(widget)
        assert widget in engine.polish.queue
        engine.polish.queue.clear()
        engine.polish.pending = False
        engine.on_parent_change(other)
        assert other not in engine.polish.queue
    finally:
        destroy(widget)
        destroy(other)


def test_parent_change_with_descendant_invalidates_subtree(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine("""
        .parent .child { background-color: red; transition: background-color 200ms; }
    """)
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    try:
        assert engine.matcher.index.flags.has_descendant is True
        engine.matcher.matching_rules(child)
        assert id(child) in engine.matcher.widget_cache.rules
        engine.on_parent_change(parent)
        qtbot.wait(20)
        # Matched after re-queue without crash
        assert engine.matcher.matching_rules(child)
    finally:
        destroy(child)
        destroy(parent)


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


def test_reload_skips_deleted_widget(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    assert _anims(engine, widget)
    wid = id(widget)
    destroy(widget)
    # Widget C++ object gone but context may linger until destroyed signal; reload must tolerate it
    _, new_rules = extract_rules(".box { background-color: green; }")
    engine.reload_rules(new_rules)
    qtbot.wait(20)
    assert wid not in engine.store.contexts or not engine.store.contexts[wid].active_animations


def test_reload_no_effect_no_radius_early_return(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine(".box { color: red; transition: color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    _, new_rules = extract_rules(".box { color: blue; transition: color 200ms; }")
    engine.reload_rules(new_rules)
    qtbot.wait(20)
    assert not engine.matcher.index.flags.has_effect
    assert not engine.matcher.index.flags.has_border_radius
    destroy(widget)


def test_reload_effect_only_widget_reevaluated(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine(".box { opacity: 0.5; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)
    _, new_rules = extract_rules(".box { opacity: 0.8; }")
    engine.reload_rules(new_rules)
    qtbot.wait(30)
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert effect.opacity() == pytest.approx(0.8)
    destroy(widget)


# ---------------------------------------------------------------------------
# window handlers
# ---------------------------------------------------------------------------


def test_window_activate_ignores_deleted_child(_app: QApplication) -> None:
    engine = make_engine(".t:active { background-color: blue; }")
    parent = QWidget()
    child = QWidget(parent)
    child.setProperty("class", "t")
    engine.seed_active_pseudo(child)
    wid = id(child)
    assert wid in engine.active_rule_widgets
    destroy(child)
    # Stale entry points at deleted C++ object → must be skipped, not crash
    engine.on_window_activate(parent)
    destroy(parent)


def test_window_deactivate_no_stuck_noop(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    parent = QWidget()
    child = QWidget(parent)
    child.setProperty("class", "box")
    engine.get_context(child)  # create empty context, no pseudos
    try:
        engine.on_window_deactivate(parent)  # no stuck pseudos → no-op
    finally:
        destroy(child)
        destroy(parent)


def test_window_deactivate_ignores_non_descendant(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    window_a = QWidget()
    window_b = QWidget()
    child_b = QWidget(window_b)
    child_b.setProperty("class", "btn")
    try:
        hover_widget(engine, child_b)
        assert ":hover" in engine.get_context(child_b).active_pseudos
        engine.on_window_deactivate(window_a)  # different window → must not clear
        assert ":hover" in engine.get_context(child_b).active_pseudos
    finally:
        destroy(child_b)
        destroy(window_a)
        destroy(window_b)


# ---------------------------------------------------------------------------
# TransitionEngine guards
# ---------------------------------------------------------------------------


def test_on_resize_guards(_app: QApplication) -> None:
    # No border-radius flag → immediate return
    engine_plain = make_engine(".box { color: red; }")
    w0 = QWidget()
    w0.setProperty("class", "box")
    try:
        engine_plain.on_resize(w0)
        assert w0 not in engine_plain.polish.queue
    finally:
        destroy(w0)

    engine = make_engine(".box { border-radius: 8px; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        # No matching radius rule → no queue
        other = QWidget()
        engine.on_resize(other)
        assert other not in engine.polish.queue
        destroy(other)
        # Running animation → no queue
        engine2 = make_engine("""
            .box { border-radius: 0px; }
            .box:hover { border-radius: 20px; transition: border-radius 1000ms; }
        """)
        w2 = QWidget()
        w2.setProperty("class", "box")
        hover_widget(engine2, w2)
        assert "border-top-left-radius" in _anims(engine2, w2)
        engine2.polish.queue.clear()
        engine2.on_resize(w2)
        assert w2 not in engine2.polish.queue
        destroy(w2)
    finally:
        destroy(widget)


def test_ensure_wa_hover_branches(_app: QApplication) -> None:
    engine = make_engine(".box:hover { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.ensure_wa_hover(widget)
        assert widget.testAttribute(Qt.WidgetAttribute.WA_Hover)
        # Already set → early return, no crash
        engine.ensure_wa_hover(widget)
    finally:
        destroy(widget)
    plain_engine = make_engine(".box { color: red; }")
    other = QWidget()
    other.setProperty("class", "box")
    try:
        plain_engine.ensure_wa_hover(other)  # no :hover rule → no attribute
        assert not other.testAttribute(Qt.WidgetAttribute.WA_Hover)
    finally:
        destroy(other)


def test_seed_active_pseudo_branches(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.seed_active_pseudo(widget)  # no :active rule → no context created
        assert id(widget) not in engine.store.contexts
    finally:
        destroy(widget)


def test_connect_checkable_branches(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    plain = QWidget()
    try:
        engine.connect_checkable(plain)  # not a button → no-op
        assert id(plain) not in engine.connected_checkable_ids
    finally:
        destroy(plain)
    btn = QCheckBox()
    btn.setProperty("class", "x")
    try:
        engine.connect_checkable(btn)
        assert id(btn) in engine.connected_checkable_ids
        engine.connect_checkable(btn)  # idempotent second call
    finally:
        destroy(btn)


def test_has_running_animation_helper(_app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        assert engine._has_running_animation(ctx) is False
        hover_widget(engine, widget)
        assert engine._has_running_animation(ctx) is True
    finally:
        destroy(widget)


def test_on_checked_changed_off(_app: QApplication) -> None:
    engine = make_engine(".t:checked { background-color: red; }")
    btn = QCheckBox()
    btn.setProperty("class", "t")
    try:
        engine.on_checked_changed(btn, True)
        assert ":checked" in engine.get_context(btn).active_pseudos
        engine.on_checked_changed(btn, False)
        assert ":checked" not in engine.get_context(btn).active_pseudos
    finally:
        destroy(btn)


# ---------------------------------------------------------------------------
# box model / clamp
# ---------------------------------------------------------------------------


def test_total_border_branches(_app: QApplication) -> None:
    btn = QPushButton("hi")
    try:
        assert total_border_px(btn, {"border-left-width": "3px"}, "left") == 3
        label = QLabel("hi")
        label.setFrameStyle(QFrame.Shape.Box.value | QFrame.Shadow.Plain.value)
        label.setLineWidth(4)
        try:
            assert total_border_px(label, {}, "left") == max(0, label.frameWidth())
        finally:
            destroy(label)
        # Style fallback path (non-QFrame, no border props)
        assert total_border_px(btn, {}, "left") >= 0
    finally:
        destroy(btn)


def test_padding_margin_invalid_returns_zero() -> None:
    assert padding_side_px({"padding-left": "auto"}, "left") == 0
    assert margin_side_px({"margin-left": "bogus"}, "left") == 0
    assert padding_side_px({}, "left") == 0


def test_content_box_qframe_height_axis(_app: QApplication) -> None:
    label = QLabel("hello")
    label.setFrameStyle(QFrame.Shape.Box.value | QFrame.Shadow.Plain.value)
    label.setLineWidth(2)
    label.resize(100, 60)
    label.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    label.show()
    try:
        raw = label.height()
        actual = content_box_px(label, {}, "height", raw)
        assert actual == raw - (raw - label.contentsRect().height())
    finally:
        destroy(label)


def test_clamp_non_px_and_non_radius_passthrough(_app: QApplication) -> None:
    widget = QWidget()
    widget.resize(100, 100)
    try:
        assert clamp_border_radius(widget, "border-top-left-radius", 50.0, "%", {}) == 50.0
        assert clamp_border_radius(widget, "width", 50.0, "px", {}) == 50.0
        assert clamp_border_radius(widget, "border-top-left-radius", 8.0, "px", {}) == 8.0
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# cache / store / suppress / widget state
# ---------------------------------------------------------------------------


def test_identity_cache_evicts_when_full() -> None:
    from qt_css_engine.css.model import StyleRule

    cache = IdentityCache()
    for i in range(2100):
        ident = WidgetIdentity(f"Tag{i}", "", frozenset({f"c{i}"}))
        cache.set(ident, [StyleRule(selector=f".c{i}", base_selector=f".c{i}", properties={})])
    assert len(cache.store) <= 2100
    assert len(cache.store) < 2100  # eviction must have dropped the oldest quarter
    cache.clear()
    assert cache.store == {}


def test_widget_cache_refs_idents_properties(_app: QApplication) -> None:
    cache = WidgetCache()
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ident = WidgetIdentity("QWidget", "", frozenset({"box"}))
        cache.set(id(widget), widget, ident, [])
        assert cache.previous_ident(id(widget)) == ident
        assert id(widget) in cache.refs
        assert id(widget) in cache.idents
        cache.invalidate(id(widget))
        assert cache.get(id(widget)) is None
    finally:
        destroy(widget)


def test_store_remove_contains_clear(_app: QApplication) -> None:
    store = WidgetStore()
    widget = QWidget()
    try:
        assert (widget in store) is False
        ctx = store.get_or_create(widget)
        assert widget in store
        assert store.get(widget) is ctx
        removed = store.remove(id(widget))
        assert removed is ctx
        assert (widget in store) is False
        store.get_or_create(widget)
        store.clear()
        assert (widget in store) is False
    finally:
        destroy(widget)


def test_suppress_nested_restores_reason() -> None:
    ctx = WidgetState()
    assert is_suppressed(None) is False
    assert is_suppressed(ctx) is False
    with suppress(ctx, InternalWriteReason.CLASS_CHANGE):
        assert is_suppressed(ctx) is True
        with suppress(ctx, InternalWriteReason.MEASURE):
            assert ctx.internal_write_reason == InternalWriteReason.MEASURE
        assert ctx.internal_write_reason == InternalWriteReason.CLASS_CHANGE
    assert is_suppressed(ctx) is False
    assert ctx.internal_write_reason is None


def test_widget_state_setters() -> None:
    ctx = WidgetState()
    ctx.active_pseudos = {":hover"}
    assert ctx.active_pseudos == {":hover"}
    ctx.css_anim_props = {"color": "red"}
    assert ctx.css_anim_props == {"color": "red"}
    ctx.active_animations = {}
    assert ctx.active_animations == {}
    ctx.class_anim_props = {"color"}
    assert ctx.class_anim_props == {"color"}
    ctx.class_anim_gen = 3
    assert ctx.class_anim_gen == 3
    ctx.class_anim_callbacks = {"a": lambda: None}
    assert "a" in ctx.class_anim_callbacks
    ctx.clicked_anim_props = {"color"}
    assert ctx.clicked_anim_props == {"color"}
    ctx.clicked_anim_gen = 2
    assert ctx.clicked_anim_gen == 2
    ctx.clicked_anim_callbacks = {"b": lambda: None}
    assert "b" in ctx.clicked_anim_callbacks
    ctx.style_box_props = {"width": "10px"}
    assert ctx.style_box_props == {"width": "10px"}
    ctx.style_flush_pending = True
    assert ctx.style_flush_pending is True
    ctx.applied_style = "x"
    assert ctx.applied_style == "x"
    ctx.style_flush_immediate = True
    assert ctx.style_flush_immediate is True
    ctx.applied_cursor = "pointer"
    assert ctx.applied_cursor == "pointer"
    ctx.pending_delays = {}
    assert ctx.pending_delays == {}
    ctx.pre_polish_size = (10, 20)
    assert ctx.pre_polish_size == (10, 20)


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------


def test_writer_schedule_unbound_uses_captured_flush(_app: QApplication, qtbot: QtBot) -> None:
    writer = StyleWriter()  # no get_ctx bound → _flush_captured path
    widget = QWidget()
    ctx = WidgetState()
    ctx.css_anim_props["color"] = "red"
    try:
        writer.schedule(widget, ctx)
        qtbot.wait(20)
        assert "color" in widget.styleSheet()
    finally:
        destroy(widget)


def test_writer_flush_captured_no_pending_noop(_app: QApplication) -> None:
    writer = StyleWriter()
    widget = QWidget()
    ctx = WidgetState()
    try:
        writer._flush_captured(widget, ctx)  # pending False → no write
        assert widget.styleSheet() == ""
    finally:
        destroy(widget)


def test_writer_flush_scheduled_guards(_app: QApplication) -> None:
    writer = StyleWriter(get_ctx=lambda _wid: None)
    widget = QWidget()
    ctx = WidgetState()
    try:
        writer.flush_scheduled(widget, 999999)  # unknown wid → no-op
        ctx.style_flush_pending = False
        writer2 = StyleWriter(get_ctx=lambda _wid: ctx)
        writer2.flush_scheduled(widget, id(widget))  # pending False → no-op
        assert widget.styleSheet() == ""
    finally:
        destroy(widget)


def test_writer_normalize_skips_without_radii_and_invalid(_app: QApplication) -> None:
    writer = StyleWriter()
    widget = QWidget()
    widget.resize(100, 100)
    try:
        ctx = WidgetState()
        ctx.css_anim_props["color"] = "red"
        writer.normalize(widget, ctx)  # no radii → early return
        assert ctx.css_anim_props["color"] == "red"
        ctx2 = WidgetState()
        ctx2.css_anim_props["border-top-left-radius"] = "auto"  # unparsable → skipped
        writer.normalize(widget, ctx2)
        assert ctx2.css_anim_props["border-top-left-radius"] == "auto"
    finally:
        destroy(widget)


def test_scoped_anim_style_reuses_selector(_app: QApplication) -> None:
    widget = QWidget()
    try:
        s1 = scoped_anim_style(widget, {"color": "red"})
        s2 = scoped_anim_style(widget, {"color": "blue"})
        assert s1.split("{")[0] == s2.split("{")[0]
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# utils — color / parsing / easing / qt helpers
# ---------------------------------------------------------------------------


def test_parse_box_shadow_multiple_takes_first() -> None:
    s = parse_box_shadow("2px 2px 4px black, 4px 4px 8px red")
    assert s is not None
    assert s.offset_x == 2.0
    assert s.color == QColor("black")


def test_parse_box_shadow_single_length_none() -> None:
    assert parse_box_shadow("5px") is None
    assert parse_box_shadow("5px solid") is None


def test_parse_box_shadow_invalid_color_defaults() -> None:
    s = parse_box_shadow("2px 2px notacolor12345")
    assert s is not None
    assert s.offset_x == 2.0
    # Invalid named color falls back to default translucent black
    assert s.color == QColor(0, 0, 0, 80)


def test_parse_color_edge_values() -> None:
    assert not parse_color("").isValid()
    assert not parse_color("rgb(1,2)").isValid()
    c = parse_color("hsl(0, 100%, 50%)")
    assert c.isValid()


def test_steps_jump_none_n1_and_cache() -> None:
    c = make_steps_curve(1, "jump-none")
    assert c.valueForProgress(0.0) == pytest.approx(0.0)
    assert c.valueForProgress(1.0) == pytest.approx(1.0)
    assert make_steps_curve(2, "end") is make_steps_curve(2, "end")
    assert make_steps_curve(1, "start").valueForProgress(0.0) == pytest.approx(1.0)


def test_cubic_bezier_factory_smoke() -> None:
    c = make_cubic_bezier_curve(0.25, 0.1, 0.25, 1.0)
    assert c.type() == QEasingCurve.Type.BezierSpline


def test_resolve_easing_steps_without_position() -> None:
    c = resolve_easing_curve("steps(3)")
    assert c.type() == QEasingCurve.Type.Custom
    assert c.valueForProgress(1.0) == pytest.approx(1.0)


def test_parse_css_numeric_edge() -> None:
    assert parse_css_numeric(None) is None
    assert parse_css_numeric("") is None
    assert parse_css_numeric("abc") is None
    assert parse_css_numeric("1.2.3px") is None
    assert parse_css_numeric("10px") == (10.0, "px")
    assert parse_css_numeric("5") == (5.0, "px")
    assert parse_css_numeric("50%") == (50.0, "%")


def test_parse_css_val_edge() -> None:
    assert parse_css_val(None) is None
    assert parse_css_val("") is None
    assert parse_css_val("auto") == "auto"
    assert parse_css_val("10") == 10
    assert parse_css_val("10.5px") == 10.5


def test_is_qobject_alive_guards() -> None:
    assert is_qobject_alive(None) is False
    w = QWidget()
    assert is_qobject_alive(w) is True
    wid_ref = weakref.ref(w)
    destroy(w)
    del w
    gc.collect()
    assert wid_ref() is None


def test_safe_disconnect_no_crash(_app: QApplication) -> None:
    widget = QWidget()
    try:
        anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=WidgetState())
        safe_disconnect(anim.anim.finished)  # nothing connected → no-op
        cb = lambda: None
        anim.anim.finished.connect(cb)
        safe_disconnect(anim.anim.finished, cb)
        safe_disconnect(anim.anim.finished, cb)  # already disconnected → swallowed
        anim.anim.stop()
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# matcher — tags, ancestors, buckets, invalidation
# ---------------------------------------------------------------------------


def test_tag_selector_matches_widget_type(_app: QApplication) -> None:
    engine = make_engine("""
        QPushButton { background-color: steelblue; }
        QPushButton:hover { background-color: royalblue; transition: background-color 200ms; }
    """)
    assert "QPushButton" in engine.matcher.index.quick.tags
    btn = QPushButton("hi")
    other = QWidget()
    try:
        hover_widget(engine, btn)
        assert "background-color" in _anims(engine, btn)
        hover_widget(engine, other)
        assert "background-color" not in _anims(engine, other)
    finally:
        destroy(btn)
        destroy(other)


def test_tag_with_class_matches(_app: QApplication) -> None:
    engine = make_engine("""
        QPushButton.btn { background-color: steelblue; }
        QPushButton.btn:hover { background-color: royalblue; transition: background-color 200ms; }
    """)
    btn_match = QPushButton("hi")
    btn_match.setProperty("class", "btn")
    btn_plain = QPushButton("hi")
    try:
        hover_widget(engine, btn_match)
        assert "background-color" in _anims(engine, btn_match)
        hover_widget(engine, btn_plain)
        assert "background-color" not in _anims(engine, btn_plain)
    finally:
        destroy(btn_match)
        destroy(btn_plain)


def test_ancestor_id_and_tag_rules(_app: QApplication) -> None:
    engine = make_engine("""
        #parent .child { background-color: red; transition: background-color 200ms; }
        QWidget .tagged { color: blue; transition: color 200ms; }
    """)
    assert "parent" in engine.matcher.index.ancestor.ids
    assert "QWidget" in engine.matcher.index.ancestor.tags
    parent = QWidget()
    parent.setObjectName("parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    tagged = QWidget(parent)
    tagged.setProperty("class", "tagged")
    try:
        hover_widget(engine, child)
        assert "background-color" in _anims(engine, child)
        engine.get_context(tagged).active_pseudos = {":hover"}
        engine.evaluate_widget_state(tagged)
        assert "color" in _anims(engine, tagged)
    finally:
        destroy(child)
        destroy(tagged)
        destroy(parent)


def test_index_rule_unconditional_bucket() -> None:
    matcher = RuleMatcher([])
    matcher._index_rule(0, CompiledSegment(None, None, frozenset()))
    assert matcher.index.buckets.unconditional == [0]


def test_index_rule_selects_rarest_class_bucket() -> None:
    """Multi-class rules index under their rarest class, deterministically.

    Regression test for the hash-seed lottery: ".rare.common" must not land in
    the big "common" bucket just because next(iter(frozenset)) happened to pick it.
    """
    engine = make_engine("""
        .common { color: red; transition: color 100ms; }
        .other.common { color: red; transition: color 100ms; }
        .rare.common { color: blue; transition: color 100ms; }
    """)
    matcher = engine.matcher
    buckets = matcher.index.buckets.by_class
    assert buckets.get("common") is not None  # single-class ".common" rule lives here
    rare_hits = buckets.get("rare", [])
    # The ".rare.common" rule (last segment has classes {"rare", "common"}) is indexed by "rare".
    matched = [matcher.rules[i] for i in rare_hits]
    assert any("rare" in (r.segments[-1] if r.segments else "") for r in matched)
    assert all("rare" in (r.segments[-1] if r.segments else "") for r in matched)


def test_multi_class_rule_matches_regardless_of_bucket_key(_app: QApplication) -> None:
    """Bucket choice is an index detail: matching results must be unaffected."""
    engine = make_engine("""
        .common { color: red; transition: color 100ms; }
        .rare.common { color: blue; transition: color 100ms; }
    """)
    both = QWidget()
    both.setProperty("class", "rare common")
    only_common = QWidget()
    only_common.setProperty("class", "common")
    try:
        assert len(engine.matcher.matching_rules(both)) == 2
        assert len(engine.matcher.matching_rules(only_common)) == 1
    finally:
        destroy(both)
        destroy(only_common)


def test_resolve_easing_curve_cubic_bezier_after_reorder() -> None:
    """The regex path still wins for cubic-bezier strings after the keyword fast-path reorder."""
    curve = resolve_easing_curve("cubic-bezier(0.4, 0, 0.2, 1)")
    assert curve.type() == QEasingCurve.Type.BezierSpline


def test_candidate_indices_include_all_buckets(_app: QApplication) -> None:
    engine = make_engine("""
        #myId { color: red; transition: color 100ms; }
        QPushButton { color: blue; transition: color 100ms; }
        .cls { color: green; transition: color 100ms; }
    """)
    widget = QPushButton("x")
    widget.setObjectName("myId")
    widget.setProperty("class", "cls")
    try:
        ident = RuleMatcher.identity(widget)
        indices = engine.matcher._candidate_indices(ident)
        assert indices == sorted(indices)
        assert len(indices) >= 3
    finally:
        destroy(widget)


def test_matches_empty_segments_false(_app: QApplication) -> None:
    from qt_css_engine.css.model import StyleRule

    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    try:
        rule = StyleRule(selector="", base_selector="", properties={}, segments=[])
        assert engine.matcher.matches(widget, rule) is False
    finally:
        destroy(widget)


def test_check_ancestors_and_single_segment(_app: QApplication) -> None:
    engine = make_engine("""
        .parent .child { color: red; }
        .solo { color: blue; }
    """)
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    solo = QWidget()
    solo.setProperty("class", "solo")
    try:
        child_rule = next(r for r in engine.matcher.rules if r.selector == ".parent .child")
        solo_rule = next(r for r in engine.matcher.rules if r.selector == ".solo")
        assert engine.matcher.check_ancestors(child, child_rule) is True
        assert engine.matcher.check_ancestors(solo, solo_rule) is True
        assert engine.matcher.matches(solo, solo_rule) is True
    finally:
        destroy(child)
        destroy(parent)
        destroy(solo)


def test_identity_matches_guards() -> None:
    ident = WidgetIdentity("QWidget", "myId", frozenset({"a"}))
    assert RuleMatcher.identity_matches(ident, compile_segment("#other")) is False
    assert RuleMatcher.identity_matches(ident, compile_segment("QPushButton")) is False
    assert RuleMatcher.identity_matches(ident, compile_segment(".missing")) is False
    assert RuleMatcher.identity_matches(ident, compile_segment("#myId.a")) is True


def test_widget_classes_and_identity_helpers(_app: QApplication) -> None:
    widget = QWidget()
    try:
        assert RuleMatcher.widget_classes(widget) == []
        widget.setProperty("class", "a b")
        assert set(RuleMatcher.widget_classes(widget)) == {"a", "b"}
        assert RuleMatcher.identity(widget).classes == frozenset({"a", "b"})
        assert RuleMatcher.identity_matches(RuleMatcher.identity(widget), compile_segment(".a")) is True
    finally:
        destroy(widget)


def test_invalidate_subtree_cleans_dead_refs(_app: QApplication) -> None:
    engine = make_engine(".parent .child { color: red; }")
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    engine.matcher.matching_rules(child)
    child_id = id(child)
    assert child_id in engine.matcher.widget_cache.rules
    destroy(child)
    del child
    gc.collect()
    # Dead weakref entry must be evicted without crash
    engine.matcher.invalidate_subtree(parent)
    assert child_id not in engine.matcher.widget_cache.rules
    destroy(parent)


def test_pseudo_machine_unknown_event_noop() -> None:
    assert PseudoMachine.update({":hover"}, QEvent.Type.Paint) == {":hover"}
    assert PseudoMachine.update(set(), QEvent.Type.Paint) == set()


def test_matcher_should_evaluate_tag_only(_app: QApplication) -> None:
    engine = make_engine("QPushButton { color: red; transition: color 100ms; }")
    assert engine.should_evaluate(QPushButton("x")) is True
    w = QWidget()
    try:
        assert engine.should_evaluate(w) is False
    finally:
        destroy(w)


# ---------------------------------------------------------------------------
# delay scheduler
# ---------------------------------------------------------------------------


def test_delay_cancel_all_and_cancel_missing(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        engine.delays.cancel(ctx, "missing")  # no timer → no-op
        engine.delays.schedule(ctx, "color", 5000, lambda: None)
        assert "color" in ctx.pending_delays
        engine.delays.cancel_all(ctx)
        assert not ctx.pending_delays
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# polish queue
# ---------------------------------------------------------------------------


def test_polish_flush_skips_running_and_dedups(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from qt_css_engine.engine.handlers.polish import PolishQueue

    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        q: PolishQueue = engine.polish
        q.queue.clear()
        q.pending = False
        evaluated: list[QWidget] = []

        def _spy(w: QWidget, cause: EvaluationCause = EvaluationCause.DIRECT, **_kwargs: object) -> None:
            evaluated.append(w)

        monkeypatch.setattr(engine, "evaluate_widget_state", _spy)
        q.enqueue(widget, schedule_flush=lambda: None)
        q.enqueue(widget, schedule_flush=lambda: None)  # duplicate in same burst
        q.flush(engine)
        # Running animation without force → skipped
        assert evaluated == []
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# invalidation + polish-burst fast paths
# ---------------------------------------------------------------------------


def test_invalidate_subtree_leaf_keeps_ancestor_cache(_app: QApplication) -> None:
    """Leaf class changes must not disturb other cached widgets."""
    engine = make_engine("""
        .outer { background-color: red; transition: background-color 100ms; }
        .outer .inner { background-color: blue; transition: background-color 100ms; }
    """)
    parent = QFrame()
    parent.setProperty("class", "outer")
    child = QLabel("x", parent)
    child.setProperty("class", "inner")
    try:
        assert engine.matcher.matching_rules(parent)
        assert engine.matcher.matching_rules(child)
        engine.matcher.invalidate_subtree(child)  # leaf: no QWidget descendants
        assert engine.matcher.widget_cache.get(id(child)) is None
        # Ancestor entry survives; descendant matches still resolve identically.
        assert engine.matcher.widget_cache.get(id(parent)) is not None
        assert any(r.selector == ".outer .inner" for r in engine.matcher.matching_rules(child))
    finally:
        destroy(parent)


def test_invalidate_subtree_non_leaf_clears_descendants(_app: QApplication) -> None:
    """Non-leaf class changes must still invalidate descendant caches."""
    engine = make_engine("""
        .outer .inner { background-color: blue; transition: background-color 100ms; }
    """)
    parent = QFrame()
    parent.setProperty("class", "outer")
    child = QLabel("x", parent)
    child.setProperty("class", "inner")
    try:
        assert any(r.selector == ".outer .inner" for r in engine.matcher.matching_rules(child))
        parent.setProperty("class", "other")
        engine.matcher.invalidate_subtree(parent)
        assert engine.matcher.widget_cache.get(id(child)) is None
        assert engine.matcher.matching_rules(child) == []
    finally:
        destroy(parent)


def test_polish_evaluate_unmatched_widget_creates_no_context(_app: QApplication) -> None:
    """POLISH evaluation below a quick-filter hit but with no structural match skips.

    `.item` is quick-relevant via the descendant rule, yet the lone widget has no
    `.container` ancestor, so nothing is engine-managed and no WidgetState is allocated.
    """
    engine = make_engine("""
        .container .item { background-color: red; transition: background-color 100ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "item")
    try:
        assert engine.should_evaluate(widget) is True
        assert engine.matcher.matching_rules(widget) == []
        engine.matcher.clear_caches()
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        assert id(widget) not in engine.store.contexts
    finally:
        destroy(widget)


def test_polish_evaluate_animated_widget_creates_context(_app: QApplication) -> None:
    """Widgets with engine-managed rules still get state on POLISH."""
    engine = make_engine(".box { background-color: red; transition: background-color 100ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        assert id(widget) in engine.store.contexts
    finally:
        destroy(widget)


def test_polish_flush_static_burst_creates_no_context(_app: QApplication) -> None:
    """Full flush over static widgets allocates no per-widget state."""
    engine = make_engine("""
        .container .item { background-color: red; transition: background-color 100ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "item")
    try:
        q = engine.polish
        q.enqueue(widget, schedule_flush=lambda: None)
        q.flush(engine)
        assert id(widget) not in engine.store.contexts
    finally:
        destroy(widget)


def test_pseudo_presence_flags(_app: QApplication) -> None:
    """Sheet-wide :hover/:active flags gate the Polish-time lookups."""
    hover_only = make_engine(".box:hover { background-color: red; transition: background-color 100ms; }")
    assert hover_only.matcher.index.flags.has_hover is True
    assert hover_only.matcher.index.flags.has_active is False
    plain = make_engine(".box { color: red; }")
    assert plain.matcher.index.flags.has_hover is False
    assert plain.matcher.index.flags.has_active is False
