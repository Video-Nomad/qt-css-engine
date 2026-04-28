# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""
Coverage tests for engine.py behaviours not covered by test_anim.py or test_interactions.py.

Focus areas:
- startup delay (animations_enabled=False)
- ID selector (#myId) and id+class (#id.cls)
- Easing curve named-string resolution
- Re-targeting a running animation mid-flight via engine
- reload_rules cancels pending delays
- _cleanup_orphans cancels pending delay for removed prop
- Pseudo specificity (:pressed > :hover)
- transition: all return trip for engine-managed props
- :focus animation end-to-end
- margin/border-radius shorthand expansion
- 3-level descendant selector
- _on_polish guards (internal write depth, active animations)
- Polish queue batches multiple widgets
- _should_evaluate true with active animation even if no current rule match
- _fire_delayed_prop no-op when prop absent from rules
- double HoverEnter is a no-op (same pseudo set)
- eventFilter ignores non-QWidget objects
"""

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.css_parser import extract_rules
from qt_css_engine.handlers import ColorAnimation, GenericPropertyAnimation
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget
from qt_css_engine.types import Animation, EvaluationCause

# ---------------------------------------------------------------------------
# Helpers (mirrors test_anim.py)
# ---------------------------------------------------------------------------


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def hover_widget(engine: TransitionEngine, widget: QWidget) -> None:
    engine._ctx(widget).active_pseudos = {":hover"}
    engine._evaluate_widget_state(widget)


def _anims(engine: TransitionEngine, widget: QWidget) -> dict[str, Animation]:
    ctx = engine._contexts.get(id(widget))
    return ctx.active_animations if ctx is not None else {}


def _has_anim(engine: TransitionEngine, widget: QWidget, prop: str) -> bool:
    return prop in _anims(engine, widget)


def _get_anim(engine: TransitionEngine, widget: QWidget, prop: str) -> Animation:
    return _anims(engine, widget)[prop]


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


class TrackedWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet_count = 0

    def setStyleSheet(self, styleSheet: str | None) -> None:
        self.setStyleSheet_count += 1
        super().setStyleSheet(styleSheet)


# ---------------------------------------------------------------------------
# Startup delay: animations suppressed until delay fires
# ---------------------------------------------------------------------------


def test_startup_delay_suppresses_animations(_app: QApplication) -> None:
    """With startup_delay_ms > 0 animations start disabled — hover must snap, not animate."""
    _, rules = extract_rules("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 500ms; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=100)
    assert not engine.animations_enabled

    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert not _has_anim(engine, widget, "background-color"), "animation must be suppressed during startup delay"
    assert "background-color" in engine._ctx(widget).css_anim_props, "value must be snapped into css_anim_props"
    destroy(widget)


def test_startup_delay_enables_animations_after_timer(_app: QApplication, qtbot: QtBot) -> None:
    """After startup_delay_ms elapses, hover must create a running animation."""
    _, rules = extract_rules("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 500ms; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=50)
    assert not engine.animations_enabled

    qtbot.wait(120)
    assert engine.animations_enabled

    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")
    assert _get_anim(engine, widget, "background-color").anim.state() == QAbstractAnimation.State.Running
    destroy(widget)


# ---------------------------------------------------------------------------
# ID selector (#myId)
# ---------------------------------------------------------------------------


def test_id_selector_populates_animated_ids() -> None:
    engine = make_engine("""
        #myBtn { background-color: steelblue; }
        #myBtn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    assert "myBtn" in engine._animated_ids
    assert "myBtn" not in engine._animated_classes
    assert "myBtn" not in engine._animated_tags


def test_id_selector_should_evaluate_matches_objectname(_app: QApplication) -> None:
    engine = make_engine("""
        #myBtn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    w_match = QWidget()
    w_match.setObjectName("myBtn")
    assert engine._should_evaluate(w_match)

    w_no_match = QWidget()
    w_no_match.setObjectName("other")
    assert not engine._should_evaluate(w_no_match)

    w_no_name = QWidget()
    assert not engine._should_evaluate(w_no_name)


def test_id_selector_creates_animation(_app: QApplication) -> None:
    engine = make_engine("""
        #myBtn { background-color: steelblue; }
        #myBtn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setObjectName("myBtn")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_id_selector_no_animation_wrong_name(_app: QApplication) -> None:
    engine = make_engine("""
        #myBtn { background-color: steelblue; }
        #myBtn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setObjectName("otherBtn")
    hover_widget(engine, widget)

    assert not _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_id_with_class_selector_matches(_app: QApplication) -> None:
    """#id.cls matches only when both objectName and class are set."""
    engine = make_engine("""
        #myBtn.active { background-color: steelblue; }
        #myBtn.active:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    # Full match: objectName + class
    widget = QWidget()
    widget.setObjectName("myBtn")
    widget.setProperty("class", "active")
    hover_widget(engine, widget)
    assert _has_anim(engine, widget, "background-color")

    # Missing class: only objectName
    widget2 = QWidget()
    widget2.setObjectName("myBtn")
    hover_widget(engine, widget2)
    assert not _has_anim(engine, widget2, "background-color")

    destroy(widget)
    destroy(widget2)


# ---------------------------------------------------------------------------
# Easing curve named-string resolution
# ---------------------------------------------------------------------------


def test_resolve_easing_curve_named_strings() -> None:
    engine = make_engine(".x { color: red; }")
    assert engine._resolve_easing_curve("ease").type() == QEasingCurve.Type.InOutQuad
    assert engine._resolve_easing_curve("ease-in").type() == QEasingCurve.Type.InCubic
    assert engine._resolve_easing_curve("ease-out").type() == QEasingCurve.Type.OutCubic
    assert engine._resolve_easing_curve("ease-in-out").type() == QEasingCurve.Type.InOutCubic
    assert engine._resolve_easing_curve("linear").type() == QEasingCurve.Type.Linear


def test_resolve_easing_curve_unknown_falls_back_to_inoutquad() -> None:
    engine = make_engine(".x { color: red; }")
    assert engine._resolve_easing_curve("not-a-valid-easing").type() == QEasingCurve.Type.InOutQuad


def test_resolve_easing_curve_step_start_and_step_end() -> None:
    engine = make_engine(".x { color: red; }")
    c_start = engine._resolve_easing_curve("step-start")
    assert c_start.type() == QEasingCurve.Type.Custom
    # step-start = steps(1, start): jumps to 1 immediately at t=0
    assert c_start.valueForProgress(0.0) == pytest.approx(1.0)

    c_end = engine._resolve_easing_curve("step-end")
    assert c_end.type() == QEasingCurve.Type.Custom
    # step-end = steps(1, end): stays at 0 until t=1
    assert c_end.valueForProgress(0.0) == pytest.approx(0.0)
    assert c_end.valueForProgress(1.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Re-targeting a running animation mid-flight via engine
# ---------------------------------------------------------------------------


def test_retarget_mid_flight_unhover_same_animation_object(_app: QApplication) -> None:
    """
    Unhovering while animation runs must re-target the same object, not create a new one.

    Transition must be on the BASE rule so `target_transitions` is populated when returning
    to base — otherwise the engine snaps instead of re-targeting.
    """
    engine = make_engine("""
        .box { background-color: steelblue; transition: background-color 1000ms; }
        .box:hover { background-color: royalblue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_before = _get_anim(engine, widget, "background-color")
    assert anim_before.anim.state() == QAbstractAnimation.State.Running

    # Advance to mid-flight so the origin-reversal seek_ms < duration (not immediate completion).
    anim_before.anim.setCurrentTime(500)

    engine._ctx(widget).active_pseudos = set()
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    anim_after = _get_anim(engine, widget, "background-color")
    assert anim_after is anim_before, "must reuse same animation object, not create new"
    assert anim_after.anim.state() == QAbstractAnimation.State.Running
    destroy(widget)


def test_retarget_mid_flight_target_is_base_color(_app: QApplication) -> None:
    """After unhover mid-flight, end target must be the base color, not the hover color."""
    engine = make_engine("""
        .box { background-color: steelblue; transition: background-color 1000ms; }
        .box:hover { background-color: royalblue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    # Advance mid-flight before reversing
    _get_anim(engine, widget, "background-color").anim.setCurrentTime(500)

    engine._ctx(widget).active_pseudos = set()
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    expected = QColor("steelblue")
    assert anim.end_color.name(QColor.NameFormat.HexArgb) == expected.name(QColor.NameFormat.HexArgb)
    destroy(widget)


# ---------------------------------------------------------------------------
# reload_rules cancels pending delays
# ---------------------------------------------------------------------------


def test_reload_cancels_pending_delays(_app: QApplication) -> None:
    """reload_rules must stop and remove all pending delay timers."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert "background-color" in ctx.pending_delays

    _, new_rules = extract_rules(".box { background-color: green; }")
    engine.reload_rules(new_rules)

    assert not ctx.pending_delays, "pending delay timer must be cancelled on reload"
    _app.processEvents()
    destroy(widget)


# ---------------------------------------------------------------------------
# _cleanup_orphans cancels pending delay for removed prop
# ---------------------------------------------------------------------------


def test_cleanup_orphans_cancels_delay_for_removed_prop(_app: QApplication) -> None:
    """_cleanup_orphans must cancel the pending timer for props no longer in any rule."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert "background-color" in ctx.pending_delays

    # Simulate re-evaluation where background-color is no longer in any rule
    engine._cleanup_orphans(widget, ctx, all_animated_props=set(), base_props={})

    assert "background-color" not in ctx.pending_delays, "delay must be cancelled for orphaned prop"
    destroy(widget)


# ---------------------------------------------------------------------------
# Pseudo specificity: :pressed (2) beats :hover (1)
# ---------------------------------------------------------------------------


def test_pseudo_specificity_pressed_beats_hover(_app: QApplication) -> None:
    """:pressed (priority 2) must win over :hover (priority 1) when both are active."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: green; transition: background-color 500ms; }
        .btn:pressed { background-color: red; transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    engine._ctx(widget).active_pseudos = {":hover", ":pressed"}
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    # :pressed target must be red, not :hover green
    assert anim.end_color.red() > 200
    assert anim.end_color.green() < 50
    destroy(widget)


def test_pseudo_specificity_hover_over_base(_app: QApplication) -> None:
    """:hover (priority 1) overrides the base state (priority 0)."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")
    hover_widget(engine, widget)

    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    expected = QColor("royalblue")
    assert anim.end_color.name(QColor.NameFormat.HexArgb) == expected.name(QColor.NameFormat.HexArgb)
    destroy(widget)


# ---------------------------------------------------------------------------
# transition: all — return trip for engine-managed props
# ---------------------------------------------------------------------------


def test_transition_all_return_trip_engine_managed_props(_app: QApplication) -> None:
    """
    When transition:all is declared and css_anim_props has a prop from a prior animation,
    that prop must be included in the return-trip animation when the class reverts.

    This exercises the code path in _collect_rule_state that expands transition:all to
    include engine-managed props already in css_anim_props.
    """
    engine = make_engine("""
        .box { background-color: red; transition: all 300ms; }
        .box.active { background-color: blue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    # Manually inject an engine-managed value: simulate a prior class animation that wrote blue.
    ctx = engine._ctx(widget)
    ctx.css_anim_props["background-color"] = "blue"

    # Evaluate in base state (.box only, no .active) — background-color is in css_anim_props
    # and must be picked up by the "transition: all" return-trip expansion.
    engine._evaluate_widget_state(widget, cause=EvaluationCause.CLASS_CHANGE)

    assert _has_anim(engine, widget, "background-color"), (
        "background-color must animate back to red via transition:all return-trip"
    )
    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    assert anim.end_color.red() > 200, "return-trip target must be red"
    destroy(widget)


# ---------------------------------------------------------------------------
# :focus animation end-to-end
# ---------------------------------------------------------------------------


def test_focus_animation_created_on_focus_in(_app: QApplication) -> None:
    """
    :focus pseudo must trigger a color animation.
    Uses background-color (not border-color shorthand) to avoid shorthand expansion mismatch
    between rule.properties keys and transition target prop names.
    """
    engine = make_engine("""
        .input { background-color: white; }
        .input:focus { background-color: lightblue; transition: background-color 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "input")

    engine._ctx(widget).active_pseudos = {":focus"}
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    assert _has_anim(engine, widget, "background-color")
    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    assert anim.anim.state() == QAbstractAnimation.State.Running
    destroy(widget)


def test_focus_animation_reversed_on_focus_out(_app: QApplication) -> None:
    """Focus-out must reverse the animation toward the base value."""
    engine = make_engine("""
        .input { background-color: white; transition: background-color 300ms; }
        .input:focus { background-color: lightblue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "input")

    engine._ctx(widget).active_pseudos = {":focus"}
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)
    assert _has_anim(engine, widget, "background-color")

    # Advance mid-flight so the reversal keeps the animation running.
    _get_anim(engine, widget, "background-color").anim.setCurrentTime(150)

    # Focus out — should re-target toward base (white)
    engine._ctx(widget).active_pseudos = set()
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    expected = QColor("white")
    assert anim.end_color.name(QColor.NameFormat.HexArgb) == expected.name(QColor.NameFormat.HexArgb)
    destroy(widget)


# ---------------------------------------------------------------------------
# margin shorthand expansion
# ---------------------------------------------------------------------------


def test_margin_shorthand_creates_four_animations(_app: QApplication) -> None:
    engine = make_engine("""
        .box { margin: 0px; }
        .box:hover { margin: 10px 20px; transition: margin 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "margin-top")
    assert _has_anim(engine, widget, "margin-right")
    assert _has_anim(engine, widget, "margin-bottom")
    assert _has_anim(engine, widget, "margin-left")
    destroy(widget)


def test_margin_shorthand_animation_targets(_app: QApplication) -> None:
    """margin: 10px 20px → top/bottom=10, right/left=20 (CSS shorthand order)."""
    engine = make_engine("""
        .box { margin: 0px; }
        .box:hover { margin: 10px 20px; transition: margin 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    top = _get_anim(engine, widget, "margin-top")
    right = _get_anim(engine, widget, "margin-right")
    assert isinstance(top, GenericPropertyAnimation)
    assert isinstance(right, GenericPropertyAnimation)
    assert top.anim.endValue() == pytest.approx(10.0)
    assert right.anim.endValue() == pytest.approx(20.0)
    destroy(widget)


# ---------------------------------------------------------------------------
# border-radius shorthand expansion
# ---------------------------------------------------------------------------


def test_border_radius_shorthand_creates_four_animations(_app: QApplication) -> None:
    engine = make_engine("""
        .box { border-radius: 0px; }
        .box:hover { border-radius: 8px; transition: border-radius 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "border-top-left-radius")
    assert _has_anim(engine, widget, "border-top-right-radius")
    assert _has_anim(engine, widget, "border-bottom-right-radius")
    assert _has_anim(engine, widget, "border-bottom-left-radius")
    destroy(widget)


def test_border_radius_all_corners_target_same_value(_app: QApplication) -> None:
    """Uniform border-radius: 8px → all four corners animate to 8."""
    engine = make_engine("""
        .box { border-radius: 0px; }
        .box:hover { border-radius: 8px; transition: border-radius 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    for corner in (
        "border-top-left-radius",
        "border-top-right-radius",
        "border-bottom-right-radius",
        "border-bottom-left-radius",
    ):
        anim = _get_anim(engine, widget, corner)
        assert isinstance(anim, GenericPropertyAnimation)
        assert anim.anim.endValue() == pytest.approx(8.0), f"{corner} wrong end value"
    destroy(widget)


# ---------------------------------------------------------------------------
# Three-level descendant selector (.a .b .c)
# ---------------------------------------------------------------------------


def test_three_level_descendant_selector_matches(_app: QApplication) -> None:
    engine = make_engine("""
        .a .b .c { background-color: steelblue; }
        .a .b .c:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    a = QWidget()
    a.setProperty("class", "a")
    b = QWidget(a)
    b.setProperty("class", "b")
    c = QWidget(b)
    c.setProperty("class", "c")

    hover_widget(engine, c)
    assert _has_anim(engine, c, "background-color")

    destroy(c)
    destroy(b)
    destroy(a)


def test_three_level_descendant_selector_no_match_wrong_middle(_app: QApplication) -> None:
    """Same last segment but wrong middle ancestor must not match .a .b .c."""
    engine = make_engine("""
        .a .b .c { background-color: steelblue; }
        .a .b .c:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    a = QWidget()
    a.setProperty("class", "a")
    wrong_mid = QWidget(a)
    wrong_mid.setProperty("class", "x")  # not .b
    c = QWidget(wrong_mid)
    c.setProperty("class", "c")

    hover_widget(engine, c)
    assert not _has_anim(engine, c, "background-color")

    destroy(c)
    destroy(wrong_mid)
    destroy(a)


# ---------------------------------------------------------------------------
# _on_polish guards
# ---------------------------------------------------------------------------


def test_on_polish_skipped_during_internal_write(_app: QApplication) -> None:
    """_on_polish must not queue the widget when internal_write_depth > 0."""
    engine = make_engine("""
        .box { background-color: red; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    ctx = engine._ctx(widget)
    ctx.internal_write_depth = 1  # engine is mid-write

    engine._polish_queue.clear()
    engine._on_polish(widget)

    assert widget not in engine._polish_queue


def test_on_polish_skipped_when_active_animation_running(_app: QApplication) -> None:
    """_on_polish must not re-queue a widget that already has running animations."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")

    engine._polish_queue.clear()
    engine._on_polish(widget)

    assert widget not in engine._polish_queue
    destroy(widget)


# ---------------------------------------------------------------------------
# Polish queue batches multiple widgets into one pass
# ---------------------------------------------------------------------------


def test_polish_queue_batches_multiple_widgets(_app: QApplication) -> None:
    """Multiple _on_polish calls before the event loop flushes must all land in the queue."""
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    w1 = QWidget()
    w1.setProperty("class", "box")
    w2 = QWidget()
    w2.setProperty("class", "box")

    engine._polish_queue.clear()
    engine._polish_pending = False

    engine._on_polish(w1)
    engine._on_polish(w2)

    assert engine._polish_pending
    assert w1 in engine._polish_queue
    assert w2 in engine._polish_queue

    destroy(w1)
    destroy(w2)


# ---------------------------------------------------------------------------
# _should_evaluate: active animation keeps widget evaluatable even if no rule matches
# ---------------------------------------------------------------------------


def test_should_evaluate_true_with_active_animation_no_rule_match(_app: QApplication) -> None:
    """A widget with active animations must pass _should_evaluate regardless of quick-filter state."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")

    # Clear all quick-filter sets to simulate "no rule ever matches anything"
    engine._animated_classes.clear()
    engine._animated_tags.clear()
    engine._animated_ids.clear()

    assert engine._should_evaluate(widget), "active animation must override quick-filter result"
    destroy(widget)


# ---------------------------------------------------------------------------
# _fire_delayed_prop: no-op when prop absent from rules
# ---------------------------------------------------------------------------


def test_fire_delayed_prop_no_op_for_prop_not_in_rules(_app: QApplication) -> None:
    """_fire_delayed_prop for a prop not in any rule must not modify css_anim_props."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = TrackedWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    count_before = widget.setStyleSheet_count
    engine._fire_delayed_prop(widget, "font-size")  # not in any rule

    assert widget.setStyleSheet_count == count_before
    destroy(widget)


# ---------------------------------------------------------------------------
# Double HoverEnter is a no-op (same pseudo set → no re-evaluation)
# ---------------------------------------------------------------------------


def test_double_hover_enter_no_re_evaluation(_app: QApplication) -> None:
    """Second HoverEnter while :hover already active must not call setStyleSheet again."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = TrackedWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)  # first hover: sets pseudos and evaluates
    count_after_first = widget.setStyleSheet_count

    # :hover already in active_pseudos; second HoverEnter must see updated == active_pseudos
    engine._ctx(widget).active_pseudos = {":hover"}
    engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))

    assert widget.setStyleSheet_count == count_after_first, "re-evaluation must not occur on duplicate HoverEnter"
    destroy(widget)


# ---------------------------------------------------------------------------
# eventFilter ignores non-QWidget objects
# ---------------------------------------------------------------------------


def test_event_filter_ignores_non_qwidget(_app: QApplication) -> None:
    """eventFilter must return False immediately for non-QWidget watched objects."""
    engine = make_engine(".box { background-color: red; }")
    non_widget = QObject()
    result = engine.eventFilter(non_widget, QEvent(QEvent.Type.Polish))
    assert result is False
