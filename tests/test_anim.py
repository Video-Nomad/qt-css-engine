# pyright: reportPrivateUsage=false
# pyright: reportUnusedParameter=false
# pyright: reportUnknownMemberType=false

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.css_parser import extract_rules
from qt_css_engine.engine import _CUBIC_BEZIER_RE, _STEPS_RE
from qt_css_engine.handlers import (
    BoxShadowHandle,
    ColorAnimation,
    GenericPropertyAnimation,
    OpacityAnimation,
)
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEasingCurve, QEvent, Qt
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import (
    QApplication,
    QCheckBox,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QWidget,
)
from qt_css_engine.types import Animation, EvaluationCause, ShadowParams, WidgetContext
from qt_css_engine.utils import (
    apply_opacity_to_widget,
    apply_shadow_to_widget,
    interpolate_oklab,
    lerp_shadow,
    make_cubic_bezier_curve,
    make_steps_curve,
    parse_box_shadow,
    parse_color,
    parse_css_val,
    shadow_as_transparent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def hover_widget(engine: TransitionEngine, widget: QWidget) -> None:
    engine._ctx(widget).active_pseudos = {":hover"}
    engine._evaluate_widget_state(widget)


def _anims(engine: TransitionEngine, widget: QWidget) -> dict[str, Animation]:
    """Return the active_animations dict for widget (empty dict if no context)."""
    ctx = engine._contexts.get(id(widget))
    if ctx is not None:
        return ctx.active_animations
    return {}


def _has_anim(engine: TransitionEngine, widget: QWidget, prop: str) -> bool:
    return prop in _anims(engine, widget)


def _get_anim(engine: TransitionEngine, widget: QWidget, prop: str) -> Animation:
    return _anims(engine, widget)[prop]


def destroy(widget: QWidget) -> None:
    """Synchronously delete the C++ widget object, firing destroyed immediately."""
    qt_delete(widget)


# ---------------------------------------------------------------------------
# Helpers – instrumented widget
# ---------------------------------------------------------------------------


class TrackedWidget(QWidget):
    """QWidget subclass that counts setStyleSheet calls."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet_count = 0

    def setStyleSheet(self, styleSheet: str | None) -> None:
        self.setStyleSheet_count += 1
        super().setStyleSheet(styleSheet)


# ---------------------------------------------------------------------------
# Widget lifetime / crash prevention
# ---------------------------------------------------------------------------


def test_watch_widget_registered_on_first_animation(app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    assert id(widget) not in engine._connected_widgets
    hover_widget(engine, widget)
    assert id(widget) in engine._connected_widgets

    destroy(widget)


def test_watch_widget_not_registered_without_animation(app: QApplication) -> None:
    """Widgets that only snap should still be registered so their context is cleaned up on destruction."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:pressed { background-color: darkblue; transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    # hover with no hover-transition → only snap, but context is still allocated and must be watched
    hover_widget(engine, widget)
    assert id(widget) in engine._connected_widgets

    destroy(widget)


def test_watch_widget_not_duplicated(app: QApplication) -> None:
    """Hovering multiple times must not register the destroyed signal twice.
    If it did, _on_widget_destroyed would run twice on the second call it would
    try to pop keys that no longer exist — the test verifies no error is raised."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)
    hover_widget(engine, widget)  # would double-connect without the guard in _watch_widget
    hover_widget(engine, widget)

    # shiboken6.delete triggers destroyed → _on_widget_destroyed runs; if double-connected
    # it would run again on already-removed keys which would KeyError
    destroy(widget)  # must not raise


def test_widget_destroyed_removed_from_active_animations(app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")

    destroy(widget)

    # After destroy, widget context should be cleaned up
    assert id(widget) not in engine._contexts


def test_widget_destroyed_removed_from_watched(app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert id(widget) in engine._connected_widgets

    destroy(widget)

    assert id(widget) not in engine._connected_widgets


def test_widget_destroyed_stops_running_animation(app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _get_anim(engine, widget, "background-color")
    assert isinstance(anim_obj, ColorAnimation)
    assert anim_obj.anim.state() == QAbstractAnimation.State.Running

    destroy(widget)

    assert anim_obj.anim.state() != QAbstractAnimation.State.Running


def test_no_crash_when_widget_deleted_during_animation(app: QApplication) -> None:
    """Core regression: deleting a widget while its animation ticks must not crash."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _get_anim(engine, widget, "background-color").anim.state() == QAbstractAnimation.State.Running

    destroy(widget)  # must not raise


def test_tick_after_widget_deleted_no_crash(app: QApplication) -> None:
    """Tick fired after C++ widget delete (queued tick race) must not raise."""
    # Color
    w1 = QWidget()
    color_anim = ColorAnimation(w1, "background-color", QColor("red"), 1000, QEasingCurve.Type.Linear)
    color_anim.set_target("blue")
    qt_delete(w1)
    color_anim._on_tick(0.5)  # must not raise
    assert color_anim.anim.state() != QAbstractAnimation.State.Running

    # Numeric
    w2 = QWidget()
    num_anim = GenericPropertyAnimation(w2, "min-width", 0, 1000, QEasingCurve.Type.Linear)
    num_anim.set_target("100px")
    qt_delete(w2)
    num_anim._on_tick(50.0)
    assert num_anim.anim.state() != QAbstractAnimation.State.Running

    # Opacity
    w3 = QWidget()
    op_anim = OpacityAnimation(w3, 1.0, 1000, QEasingCurve.Type.Linear)
    op_anim.anim.setStartValue(1.0)
    op_anim.anim.setEndValue(0.0)
    op_anim.anim.start()
    qt_delete(w3)
    op_anim._on_tick(0.5)
    assert op_anim.anim.state() != QAbstractAnimation.State.Running

    # BoxShadow
    w4 = QWidget()
    shadow_anim = BoxShadowHandle(w4, "none", 1000, QEasingCurve.Type.Linear)
    shadow_anim.set_target("2px 2px 4px black")
    qt_delete(w4)
    shadow_anim._on_tick(0.5)
    assert shadow_anim.anim.state() != QAbstractAnimation.State.Running


def test_multiple_animated_props_all_cleaned_up(app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; color: white; }
        .box:hover {
            background-color: royalblue;
            color: black;
            transition: background-color 1000ms;
            transition: color 800ms;
        }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")
    assert _has_anim(engine, widget, "color")

    destroy(widget)

    assert not _anims(engine, widget)


# ---------------------------------------------------------------------------
# Batched setStyleSheet
# ---------------------------------------------------------------------------


def test_snap_multiple_props_calls_setStyleSheet_once(app: QApplication) -> None:
    """When N props snap on one state change, setStyleSheet is called once, not N times."""
    engine = make_engine("""
        .btn { background-color: steelblue; color: white; border-color: gray; }
        .btn:pressed {
            background-color: black; color: yellow; border-color: red;
            transition: background-color 500ms, color 400ms, border-color 300ms;
        }
    """)
    widget = TrackedWidget()
    widget.setProperty("class", "btn")

    # Disable animations so all 3 pressed props snap instead of animate → one batched setStyleSheet
    engine.animations_enabled = False
    engine._ctx(widget).active_pseudos = {":pressed"}
    engine._evaluate_widget_state(widget)

    assert widget.setStyleSheet_count == 1
    destroy(widget)


def test_snap_applies_all_values_in_one_call(app: QApplication) -> None:
    """The single setStyleSheet call after snapping must contain ALL snapped property values."""
    engine = make_engine("""
        .btn { background-color: steelblue; color: white; }
        .btn:pressed {
            background-color: black; color: yellow;
            transition: background-color 500ms, color 400ms;
        }
    """)
    widget = TrackedWidget()
    widget.setProperty("class", "btn")

    # Disable animations so both pressed props snap → inline style must contain both
    engine.animations_enabled = False
    engine._ctx(widget).active_pseudos = {":pressed"}
    engine._evaluate_widget_state(widget)

    final_style = widget.styleSheet()
    assert "background-color" in final_style
    assert "color" in final_style
    destroy(widget)


def test_no_setStyleSheet_when_only_opacity_snaps(app: QApplication) -> None:
    """OpacityAnimation uses QGraphicsOpacityEffect, not the stylesheet — no setStyleSheet call."""
    engine = make_engine("""
        .box { opacity: 1; }
        .box:pressed { opacity: 0.5; transition: opacity 300ms; }
    """)
    widget = TrackedWidget()
    widget.setProperty("class", "box")

    # Hover with no hover-transition → opacity snaps via setOpacity, not setStyleSheet
    engine._ctx(widget).active_pseudos = {":hover"}
    engine._evaluate_widget_state(widget)

    assert widget.setStyleSheet_count == 0
    destroy(widget)


# ---------------------------------------------------------------------------
# Property alias: background / background-color treated as one animated prop
# ---------------------------------------------------------------------------


def test_background_alias_creates_single_animation(app: QApplication) -> None:
    """background: and background-color: must map to the same animation key."""
    engine = make_engine("""
        .box { background: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_keys = list(_anims(engine, widget).keys())
    assert len(anim_keys) == 1
    assert anim_keys[0] == "background-color"

    destroy(widget)


def test_background_alias_reverse_creates_single_animation(app: QApplication) -> None:
    """transition: background (alias) + background-color in hover → one animation, not two."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background: royalblue; transition: background 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_keys = list(_anims(engine, widget).keys())
    assert len(anim_keys) == 1
    assert anim_keys[0] == "background-color"

    destroy(widget)


# ---------------------------------------------------------------------------
# Shorthand expansion: padding creates per-side animations
# ---------------------------------------------------------------------------


def test_padding_shorthand_creates_four_animations(app: QApplication) -> None:
    engine = make_engine("""
        .box { padding: 10px 20px; }
        .box:hover { padding: 5px 15px; transition: padding 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "padding-top")
    assert _has_anim(engine, widget, "padding-right")
    assert _has_anim(engine, widget, "padding-bottom")
    assert _has_anim(engine, widget, "padding-left")

    destroy(widget)


def test_padding_shorthand_animation_targets_correct_values(app: QApplication) -> None:
    """Each padding side's animation should target the expanded hover value."""
    engine = make_engine("""
        .box { padding: 10px 20px; }
        .box:hover { padding: 5px 15px; transition: padding 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    top_anim = _get_anim(engine, widget, "padding-top")
    right_anim = _get_anim(engine, widget, "padding-right")
    assert isinstance(top_anim, GenericPropertyAnimation)
    assert isinstance(right_anim, GenericPropertyAnimation)
    # end values should match hover's expanded padding: 5px 15px
    assert top_anim.anim.endValue() == 5.0
    assert right_anim.anim.endValue() == 15.0

    destroy(widget)


# ---------------------------------------------------------------------------
# border: shorthand creates per-side width + color animations
# ---------------------------------------------------------------------------


def test_border_shorthand_creates_width_and_color_animations(app: QApplication) -> None:
    engine = make_engine("""
        .box { border: 1px solid gray; }
        .box:hover { border: 3px solid white; transition: border 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "border-top-width")
    assert _has_anim(engine, widget, "border-right-width")
    assert _has_anim(engine, widget, "border-bottom-width")
    assert _has_anim(engine, widget, "border-left-width")
    assert _has_anim(engine, widget, "border-top-color")
    assert _has_anim(engine, widget, "border-right-color")
    assert _has_anim(engine, widget, "border-bottom-color")
    assert _has_anim(engine, widget, "border-left-color")

    destroy(widget)


def test_border_shorthand_no_style_animation(app: QApplication) -> None:
    """border-style is not animatable — no animation object should be created for it."""
    engine = make_engine("""
        .box { border: 1px solid gray; }
        .box:hover { border: 3px solid white; transition: border 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert not _has_anim(engine, widget, "border-style")

    destroy(widget)


# ---------------------------------------------------------------------------
# Zero-duration transition: snap immediately, no animation object created
# ---------------------------------------------------------------------------


def test_zero_duration_creates_no_animation_object(app: QApplication) -> None:
    """transition: prop 0s must not create an animation object — snap directly."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert not _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_zero_duration_snaps_value_into_css_anim_props(app: QApplication) -> None:
    """With 0-duration, the target value must be written to _css_anim_props immediately."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    props: dict[str, str] = engine._ctx(widget).css_anim_props
    assert "background-color" in props
    destroy(widget)


def test_zero_duration_registers_watched_widget(app: QApplication) -> None:
    """Widget with zero-duration transition still gets registered so its context is cleaned up on destruction."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert id(widget) in engine._connected_widgets
    destroy(widget)


def test_zero_duration_stops_existing_animation(app: QApplication) -> None:
    """If a prior non-zero animation exists and duration drops to 0, it must be stopped and snapped."""

    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _anims(engine, widget).get("background-color")
    assert isinstance(anim_obj, ColorAnimation)
    assert anim_obj.anim.state() == QAbstractAnimation.State.Running

    # Now switch to a 0-duration engine (simulates stylesheet change to 0s)
    engine2 = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 0ms; }
    """)
    # Inject the existing anim_obj into engine2 so it exercises the snap_to branch
    engine2._ctx(widget).active_animations["background-color"] = anim_obj
    engine2._ctx(widget).active_pseudos = {":hover"}
    engine2._evaluate_widget_state(widget)

    assert anim_obj.anim.state() != QAbstractAnimation.State.Running
    destroy(widget)


def test_id_reuse_no_ghost_animations(app: QApplication) -> None:
    """After a widget is destroyed, its id() slot in active_animations is clear,
    so a new widget that happens to reuse the same Python id does not inherit
    stale animation objects."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)

    w1 = QWidget()
    w1.setProperty("class", "box")
    hover_widget(engine, w1)
    destroy(w1)

    assert not _anims(engine, w1)
    assert id(w1) not in engine._connected_widgets


# ---------------------------------------------------------------------------
# Compound class selector: .btn.active {}
# ---------------------------------------------------------------------------


def test_compound_class_selector_matches_widget_with_both_classes(app: QApplication) -> None:
    """A widget with class='btn active' must match .btn.active rules."""
    engine = make_engine("""
        .btn.active { background-color: steelblue; }
        .btn.active:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn active")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_compound_class_selector_no_match_missing_one_class(app: QApplication) -> None:
    """A widget with only class='btn' must NOT match .btn.active."""
    engine = make_engine("""
        .btn.active { background-color: steelblue; }
        .btn.active:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")
    hover_widget(engine, widget)

    assert not _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_compound_class_selector_no_match_no_classes(app: QApplication) -> None:
    """A widget with no class property must NOT match .btn.active."""
    engine = make_engine("""
        .btn.active { background-color: steelblue; }
        .btn.active:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    hover_widget(engine, widget)

    assert not _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_compound_class_selector_extra_classes_still_match(app: QApplication) -> None:
    """A widget with class='btn active extra' must still match .btn.active."""
    engine = make_engine("""
        .btn.active { background-color: steelblue; }
        .btn.active:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn active extra")
    hover_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_compound_class_selector_three_classes(app: QApplication) -> None:
    """Selectors with three compound classes like .a.b.c match only when all are present."""
    engine = make_engine("""
        .a.b.c { background-color: steelblue; }
        .a.b.c:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget_match = QWidget()
    widget_match.setProperty("class", "a b c")
    widget_no_match = QWidget()
    widget_no_match.setProperty("class", "a b")

    hover_widget(engine, widget_match)
    hover_widget(engine, widget_no_match)

    assert _has_anim(engine, widget_match, "background-color")
    assert not _has_anim(engine, widget_no_match, "background-color")

    destroy(widget_match)
    destroy(widget_no_match)


def test_compound_class_selector_segments_parsed_correctly() -> None:
    """segments for .btn.active must be a single compound segment, not two."""

    _, rules = extract_rules(".btn.active { color: red; }")
    rule = rules[0]
    assert rule.segments == [".btn.active"]
    assert rule.base_selector == ".btn.active"


# ---------------------------------------------------------------------------
# Dynamic class property change: widget.setProperty("class", ...)
# ---------------------------------------------------------------------------


def test_class_change_to_compound_triggers_animation(app: QApplication) -> None:
    """Changing class from 'btn' to 'btn active' must trigger the .btn.active:hover animation."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn.active { background-color: gold; }
        .btn.active:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    app.installEventFilter(engine)
    widget = QWidget()
    widget.setProperty("class", "btn")

    # Hover while only .btn — no .btn.active rule matches, no animation
    hover_widget(engine, widget)
    assert not _has_anim(engine, widget, "background-color")

    # Dynamically promote to .btn.active — DynamicPropertyChange fires → engine re-evaluates
    widget.setProperty("class", "btn active")
    app.processEvents()

    # Now hover should match .btn.active:hover
    hover_widget(engine, widget)
    assert _has_anim(engine, widget, "background-color")

    app.removeEventFilter(engine)
    destroy(widget)


def test_class_change_from_compound_snaps_animation_to_base(app: QApplication) -> None:
    """
    Removing a class ('btn active' → 'btn') while an animation is running must snap and clean up.

    Orphan cleanup snaps to base value, removes the entry from active_animations, and schedules
    the handler for deletion — it must not remain as a dangling entry.
    """
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn.active { background-color: gold; }
        .btn.active:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    app.installEventFilter(engine)
    widget = QWidget()
    widget.setProperty("class", "btn active")
    hover_widget(engine, widget)

    anim_key = "background-color"
    assert anim_key in _anims(engine, widget)
    assert _anims(engine, widget)[anim_key].anim.state() == QAbstractAnimation.State.Running

    # Demote to plain .btn — no .btn:hover rule, orphan cleanup snaps and removes
    widget.setProperty("class", "btn")
    app.processEvents()

    assert anim_key not in _anims(engine, widget)

    app.removeEventFilter(engine)
    destroy(widget)


def test_class_change_animation_defers_hover(app: QApplication) -> None:
    """
    Hover during a class-change animation must NOT redirect the animation target.

    The class-change animation completes first; only then does hover take effect.
    """
    engine = make_engine("""
        .btn { background-color: #444444; transition: background-color 300ms ease; }
        .btn.active { background-color: #4d88ff; }
        .btn:hover { background-color: #666666; }
        .btn.active:hover { background-color: #3366cc; }
    """)
    app.installEventFilter(engine)
    widget = QWidget()
    widget.setProperty("class", "btn")
    app.processEvents()

    # Activate → class-change animation starts (#444444 → #4d88ff)
    widget.setProperty("class", "btn active")
    app.processEvents()

    anim_key = "background-color"
    assert _has_anim(engine, widget, anim_key)
    anim = _get_anim(engine, widget, anim_key)
    assert anim.anim.state() == QAbstractAnimation.State.Running

    # Hover while class-change anim is running — should NOT redirect target
    end_before = anim.end_color.name(QColor.NameFormat.HexArgb)  # type: ignore[union-attr]
    hover_widget(engine, widget)
    end_after = anim.end_color.name(QColor.NameFormat.HexArgb)  # type: ignore[union-attr]
    assert end_before == end_after, "Hover must not redirect class-change animation target"

    app.removeEventFilter(engine)
    destroy(widget)


def test_class_change_multi_prop_hover_unblocks_per_prop(app: QApplication) -> None:
    """
    Each class-animated prop must unblock hover independently when its own animation finishes.

    Regression: the old implementation only re-evaluated when ALL class-animated props
    were done, meaning a fast-finishing prop (border-color 100ms) stayed blocked until
    the slowest prop (background-color 300ms) finished.
    """
    # Use two direct color props (no shorthand expansion) with different durations.
    engine = make_engine("""
        .btn {
            background-color: #444444;
            color: #ffffff;
            transition: background-color 300ms, color 100ms;
        }
        .btn.active { background-color: #4d88ff; color: #000000; }
        .btn:hover { background-color: #666666; color: #cccccc; }
    """)
    app.installEventFilter(engine)
    widget = QWidget()
    widget.setProperty("class", "btn")
    app.processEvents()

    # Class change → both animations start
    widget.setProperty("class", "btn active")
    app.processEvents()

    assert _has_anim(engine, widget, "background-color")
    assert _has_anim(engine, widget, "color")
    ctx = engine._ctx(widget)
    assert "background-color" in ctx.class_anim_props
    assert "color" in ctx.class_anim_props

    # Hover while both class animations running — both should be blocked
    hover_widget(engine, widget)
    bg_anim = _get_anim(engine, widget, "background-color")
    cl_anim = _get_anim(engine, widget, "color")
    assert bg_anim.end_color.name(QColor.NameFormat.HexArgb) == QColor("#4d88ff").name(  # type: ignore[union-attr]
        QColor.NameFormat.HexArgb
    ), "background-color class anim must not be redirected to hover while blocked"
    assert cl_anim.end_color.name(QColor.NameFormat.HexArgb) == QColor("#000000").name(  # type: ignore[union-attr]
        QColor.NameFormat.HexArgb
    ), "color class anim must not be redirected to hover while blocked"

    # Simulate color finishing first (shorter duration)
    cl_anim.anim.stop()
    cl_anim.anim.finished.emit()
    app.processEvents()

    # color should now target the hover value; background-color still class-animating
    assert "color" not in ctx.class_anim_props, "color must be unblocked after its class anim finishes"
    assert "background-color" in ctx.class_anim_props, "background-color must still be blocked"
    cl_anim_after = _get_anim(engine, widget, "color")
    assert cl_anim_after.end_color.name(QColor.NameFormat.HexArgb) == QColor("#cccccc").name(  # type: ignore[union-attr]
        QColor.NameFormat.HexArgb
    ), "color must target hover value once its class anim is done"

    # Simulate background-color finishing
    bg_anim.anim.stop()
    bg_anim.anim.finished.emit()
    app.processEvents()

    assert not ctx.class_anim_props
    bg_anim_after = _get_anim(engine, widget, "background-color")
    assert bg_anim_after.end_color.name(QColor.NameFormat.HexArgb) == QColor("#666666").name(  # type: ignore[union-attr]
        QColor.NameFormat.HexArgb
    ), "background-color must target hover value after its class anim finishes"

    app.removeEventFilter(engine)
    destroy(widget)


def test_class_change_animation_finished_then_hover_applies(app: QApplication) -> None:
    """After class-change animation finishes, pending hover must take effect."""
    engine = make_engine("""
        .btn { background-color: #444444; transition: background-color 50ms ease; }
        .btn.active { background-color: #4d88ff; }
        .btn:hover { background-color: #666666; }
        .btn.active:hover { background-color: #3366cc; }
    """)
    app.installEventFilter(engine)
    widget = QWidget()
    widget.setProperty("class", "btn")
    app.processEvents()

    # Activate → short animation starts
    widget.setProperty("class", "btn active")
    app.processEvents()

    anim_key = "background-color"
    assert _has_anim(engine, widget, anim_key)

    # Hover while animation still running
    hover_widget(engine, widget)

    # Let animation finish
    anim = _get_anim(engine, widget, anim_key)
    anim.anim.stop()
    # Simulate finished signal
    anim.anim.finished.emit()
    app.processEvents()

    # Now hover should be applied (re-evaluated after class anim done)
    anim2 = _get_anim(engine, widget, anim_key)
    end_color = anim2.end_color.name(QColor.NameFormat.HexArgb)  # type: ignore[union-attr]
    # Should target .btn.active:hover color
    assert end_color == QColor("#3366cc").name(QColor.NameFormat.HexArgb)

    app.removeEventFilter(engine)
    destroy(widget)


def test_class_change_unrelated_property_ignored(app: QApplication) -> None:
    """setProperty() for a non-class property must not trigger re-evaluation."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 300ms; }
    """)
    app.installEventFilter(engine)
    widget = TrackedWidget()
    widget.setProperty("class", "btn")
    app.processEvents()  # flush any pending Polish events from the class assignment

    before = widget.setStyleSheet_count
    widget.setProperty("data-foo", "bar")  # unrelated dynamic property
    app.processEvents()
    after = widget.setStyleSheet_count

    assert after == before  # no re-evaluation triggered

    app.removeEventFilter(engine)
    destroy(widget)


def test_class_change_skips_unrelated_widget(app: QApplication) -> None:
    """
    Regression: class change on a widget that matches no animated rule must not
    create an engine context for it.  Before the fix _on_class_change ran
    unpolish/polish unconditionally on every widget in the app.
    """
    engine = make_engine("""
        .animated { background-color: red; transition: background-color 300ms; }
    """)
    app.installEventFilter(engine)

    unrelated = QWidget()
    engine._on_class_change(unrelated)  # no class → no match

    assert engine._contexts.get(id(unrelated)) is None, (
        "engine must not create a context for a widget that matches no animated rule"
    )

    unrelated.setProperty("class", "unrelated")
    engine._on_class_change(unrelated)  # wrong class → still no match

    assert engine._contexts.get(id(unrelated)) is None

    app.removeEventFilter(engine)
    destroy(unrelated)


def test_class_change_does_not_call_setStyleSheet_on_unmatched_widget(app: QApplication) -> None:
    """
    Regression: class change on an unmatched widget must not trigger setStyleSheet,
    which could discard a widget-level setStyle() override (e.g. QFusionStyle).
    """
    engine = make_engine("""
        .animated { background-color: red; transition: background-color 300ms; }
    """)
    app.installEventFilter(engine)

    unrelated = TrackedWidget()
    unrelated.setProperty("class", "not-animated")
    app.processEvents()

    before = unrelated.setStyleSheet_count
    engine._on_class_change(unrelated)
    app.processEvents()

    assert unrelated.setStyleSheet_count == before, "engine must not call setStyleSheet on a widget it does not manage"

    app.removeEventFilter(engine)
    destroy(unrelated)


# ---------------------------------------------------------------------------
# parse_color — unit tests (no QApplication needed)
# ---------------------------------------------------------------------------


def test_parse_color_hex() -> None:
    c = parse_color("#1e90ff")
    assert c.isValid()
    assert c.red() == 0x1E
    assert c.green() == 0x90
    assert c.blue() == 0xFF


def test_parse_color_named() -> None:
    assert parse_color("steelblue").isValid()
    assert parse_color("red").isValid()


def test_parse_color_rgb() -> None:
    c = parse_color("rgb(30, 144, 255)")
    assert c.isValid()
    assert c.red() == 30
    assert c.green() == 144
    assert c.blue() == 255
    assert c.alpha() == 255


def test_parse_color_rgba() -> None:
    c = parse_color("rgba(30, 144, 255, 0.5)")
    assert c.isValid()
    assert c.red() == 30
    assert c.green() == 144
    assert c.blue() == 255
    assert c.alpha() == round(0.5 * 255)  # 128 (banker's rounding)


def test_parse_color_rgba_fully_transparent() -> None:
    c = parse_color("rgba(0, 0, 0, 0)")
    assert c.isValid()
    assert c.alpha() == 0


def test_parse_color_rgba_fully_opaque() -> None:
    c = parse_color("rgba(255, 255, 255, 1)")
    assert c.isValid()
    assert c.alpha() == 255


def test_parse_color_hsl() -> None:
    c = parse_color("hsl(207, 44%, 49%)")
    assert c.isValid()


def test_parse_color_hsla() -> None:
    c = parse_color("hsla(207, 44%, 49%, 0.5)")
    assert c.isValid()
    assert c.alpha() == round(0.5 * 255)


def test_parse_color_case_insensitive() -> None:
    c = parse_color("RGB(30, 144, 255)")
    assert c.isValid()
    assert c.red() == 30
    assert c.green() == 144
    assert c.blue() == 255


def test_parse_color_invalid_returns_invalid_qcolor() -> None:
    assert not parse_color("not-a-color").isValid()
    assert not parse_color("rgb(bad)").isValid()


# ---------------------------------------------------------------------------
# parse_color — end-to-end: ColorAnimation accepts functional color formats
# ---------------------------------------------------------------------------


def test_color_animation_rgba_base_starts_valid(app: QApplication) -> None:
    """ColorAnimation must start with a valid QColor when the base uses rgba(...)."""
    engine = make_engine("""
        .box { background-color: rgba(30, 144, 255, 1.0); }
        .box:hover { background-color: rgba(65, 105, 225, 1.0); transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _anims(engine, widget).get("background-color")
    assert isinstance(anim_obj, ColorAnimation)
    assert anim_obj.anim.state() == QAbstractAnimation.State.Running
    assert anim_obj.start_color.isValid()

    destroy(widget)


def test_color_animation_hsl_hover_target_is_running(app: QApplication) -> None:
    """ColorAnimation must animate when the hover target is an hsl(...) value."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: hsl(225, 73%, 57%); transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _anims(engine, widget).get("background-color")
    assert isinstance(anim_obj, ColorAnimation)
    assert anim_obj.anim.state() == QAbstractAnimation.State.Running

    destroy(widget)


def test_color_animation_rgba_with_alpha_snap(app: QApplication) -> None:
    """snap_to with an rgba value must update current_color without crashing."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: rgba(65, 105, 225, 0.8); transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _anims(engine, widget).get("background-color")
    assert isinstance(anim_obj, ColorAnimation)

    anim_obj.snap_to("rgba(65, 105, 225, 0.5)")
    assert anim_obj.current_color.isValid()
    assert anim_obj.current_color.alpha() == round(0.5 * 255)

    destroy(widget)


def test_color_animation_tick_preserves_alpha_in_stylesheet(app: QApplication) -> None:
    """_on_tick must write #aarrggbb so the alpha channel is not silently dropped."""
    engine = make_engine("""
        .box { background-color: rgba(30, 144, 255, 1.0); }
        .box:hover { background-color: rgba(65, 105, 225, 0.5); transition: background-color 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _anims(engine, widget).get("background-color")
    assert isinstance(anim_obj, ColorAnimation)

    # Simulate a tick at t=1.0 (fully transitioned to the hover color, which is semi-transparent)
    anim_obj._on_tick(1.0)

    props: dict[str, str] = engine._ctx(widget).css_anim_props
    stored = props.get("background-color", "")
    # Must be 9-char #aarrggbb, not 7-char #rrggbb
    assert stored.startswith("#") and len(stored) == 9, f"Expected #aarrggbb, got {stored!r}"

    destroy(widget)


# ---------------------------------------------------------------------------
# Subcontrol selectors: ::item, ::handle, etc.
# ---------------------------------------------------------------------------


def test_subcontrol_rule_creates_no_animation(app: QApplication) -> None:
    """::item rules cannot match real widgets; hovering the parent creates no animation."""
    engine = make_engine("""
        .results-list-view::item { background-color: transparent; transition: background 0.3s ease; }
        .results-list-view::item:hover { background-color: rgba(128, 130, 158, 0.1); }
    """)
    widget = QWidget()
    widget.setProperty("class", "results-list-view")
    hover_widget(engine, widget)

    assert not _anims(engine, widget)
    destroy(widget)


def test_subcontrol_rules_not_added_to_quick_filters() -> None:
    """Subcontrol transitions are zeroed, so they must not pollute the quick-filter sets."""
    engine = make_engine("""
        .results-list-view::item { background-color: transparent; transition: background 0.3s ease; }
        .results-list-view::item:hover { background-color: rgba(128, 130, 158, 0.1); }
    """)
    assert "results-list-view" not in engine._animated_classes
    assert not engine._animated_tags
    assert not engine._animated_ids


def test_subcontrol_sibling_rule_still_animates(app: QApplication) -> None:
    """A ::item rule must not interfere with a normal rule on the same parent widget."""
    engine = make_engine("""
        .results-list-view { background-color: steelblue; }
        .results-list-view:hover { background-color: royalblue; transition: background-color 300ms; }
        .results-list-view::item { background-color: transparent; transition: background 0.3s ease; }
        .results-list-view::item:hover { background-color: rgba(128, 130, 158, 0.1); }
    """)
    widget = QWidget()
    widget.setProperty("class", "results-list-view")
    hover_widget(engine, widget)

    # The widget-level :hover animation must still be created
    assert _has_anim(engine, widget, "background-color")
    destroy(widget)


def test_opacity_initialization_when_animations_disabled(app: QApplication) -> None:
    """Opacity must still be applied via QGraphicsOpacityEffect even if animations are disabled."""
    engine = make_engine(
        """
        .box { opacity: 0.5; transition: opacity 500ms; }
    """
    )
    engine.animations_enabled = False
    widget = QWidget()
    widget.setProperty("class", "box")

    # Trigger polish to evaluate state
    engine._evaluate_widget_state(widget)

    anim_obj = _anims(engine, widget).get("opacity")
    assert isinstance(anim_obj, OpacityAnimation)
    assert anim_obj._current_val == 0.5


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def test_interpolate_oklab() -> None:
    c1 = QColor(255, 0, 0, 255)  # Red
    c2 = QColor(0, 0, 255, 128)  # Blue semi-transparent

    mid = interpolate_oklab(c1, c2, 0.5)
    assert mid.isValid()
    assert mid.alpha() == round(255 + (128 - 255) * 0.5)

    c_start = interpolate_oklab(c1, c2, 0.0)
    assert c_start.red() == 255
    assert c_start.alpha() == 255

    c_end = interpolate_oklab(c1, c2, 1.0)
    assert c_end.blue() == 255
    assert c_end.alpha() == 128


def test_parse_box_shadow() -> None:
    # None cases
    assert parse_box_shadow("") is None
    assert parse_box_shadow("none") is None
    assert parse_box_shadow("inset 5px 5px black") is None

    # Parsing valid
    s = parse_box_shadow("2px 4px 8px 1px rgba(0,0,0,0.5)")
    assert s is not None
    assert s.offset_x == 2.0
    assert s.offset_y == 4.0
    assert s.blur == 8.0
    assert s.spread == 1.0
    assert s.color.alpha() == 128

    # Default color and partial lengths
    s2 = parse_box_shadow("10px -5px red")
    assert s2 is not None
    assert s2.offset_x == 10.0
    assert s2.offset_y == -5.0
    assert s2.blur == 0.0
    assert s2.spread == 0.0
    assert s2.color == QColor("red")

    # Single non-inset shadow works
    s4 = parse_box_shadow("2px 2px blue")
    assert s4 is not None
    assert s4.offset_x == 2.0
    assert s4.color == QColor("blue")


def test_parse_css_val() -> None:
    assert parse_css_val(None) is None
    assert parse_css_val("") is None
    assert parse_css_val("10px") == 10
    assert parse_css_val("10.5px") == 10.5
    assert parse_css_val("-5px") == -5
    assert parse_css_val("auto") == "auto"


def test_shadow_as_transparent() -> None:
    s = ShadowParams(1, 2, 3, 4, QColor(255, 0, 0, 255))
    s_transparent = shadow_as_transparent(s)
    assert s_transparent.offset_x == 1
    assert s_transparent.color.alpha() == 0
    assert s_transparent.color.red() == 255


def test_lerp_shadow() -> None:
    s1 = ShadowParams(0, 0, 0, 0, QColor(255, 0, 0, 255))
    s2 = ShadowParams(10, 10, 10, 10, QColor(0, 0, 255, 128))
    s_mid = lerp_shadow(s1, s2, 0.5)
    assert s_mid.offset_x == 5.0
    assert s_mid.blur == 5.0
    assert s_mid.color.alpha() == round(255 + (128 - 255) * 0.5)


# ---------------------------------------------------------------------------
# Animation & Effect Logic
# ---------------------------------------------------------------------------


def test_box_shadow_handle_snap_and_tick(app: QApplication) -> None:
    widget = QWidget()
    handle = BoxShadowHandle(widget, "2px 2px 2px black", 100, QEasingCurve.Type.Linear)

    # Tick simulation (e.g. at 50% start=None if snapped, no crash)
    assert handle._current is not None
    assert handle._current.offset_x == 2.0

    # Set target
    handle.set_target("10px 10px 10px red")
    assert handle._start is not None
    assert handle._end is not None
    assert handle.anim.state() == QAbstractAnimation.State.Running

    # Tick midway
    handle._on_tick(0.5)
    assert handle._current.offset_x == 6.0

    # Snap
    handle.snap_to("none")
    assert handle._start is None
    assert handle._end is None
    assert handle._current is None

    destroy(widget)


def test_generic_property_animation(app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "padding-top", 10.0, 100, QEasingCurve.Type.Linear, ctx=ctx)

    # Check start
    assert anim.current_val == 10.0

    # Set target
    anim.set_target("20px")
    assert anim.anim.state() == QAbstractAnimation.State.Running

    # Tick
    anim._on_tick(15.0)
    assert ctx.css_anim_props.get("padding-top") == "15.000px"

    # Snap
    anim.snap_to("-5px")
    assert anim.current_val == -5.0
    assert ctx.css_anim_props.get("padding-top") == "-5.000px"

    destroy(widget)


# ---------------------------------------------------------------------------
# TransitionEngine Events & Advanced Mechanics
# ---------------------------------------------------------------------------


def test_transition_engine_event_filter_pseudos(app: QApplication) -> None:
    engine = make_engine(".btn:focus { background-color: blue; } .btn:pressed { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "btn")

    # Focus In
    event_focus_in = QEvent(QEvent.Type.FocusIn)
    engine.eventFilter(widget, event_focus_in)
    assert ":focus" in engine._ctx(widget).active_pseudos

    # Mouse Button Press
    event_mouse_press = QEvent(QEvent.Type.MouseButtonPress)
    engine.eventFilter(widget, event_mouse_press)
    assert ":pressed" in engine._ctx(widget).active_pseudos

    # Mouse Button Release
    event_mouse_release = QEvent(QEvent.Type.MouseButtonRelease)
    engine.eventFilter(widget, event_mouse_release)
    assert ":pressed" not in engine._ctx(widget).active_pseudos

    # Focus Out
    event_focus_out = QEvent(QEvent.Type.FocusOut)
    engine.eventFilter(widget, event_focus_out)
    assert ":focus" not in engine._ctx(widget).active_pseudos

    destroy(widget)


def test_transition_engine_hierarchy_matches() -> None:
    # Assuming testing _matches traverses hierarchy properly
    engine = make_engine(".parent .child { background-color: blue; }")

    parent = QWidget()
    parent.setProperty("class", "parent")

    child = QWidget(parent)
    child.setProperty("class", "child")

    assert engine._matches(child, engine.rules[0]) is True
    assert engine._matches(parent, engine.rules[0]) is False

    # Extra test: false match
    fake_parent = QWidget()
    fake_parent.setProperty("class", "not-parent")
    child2 = QWidget(fake_parent)
    child2.setProperty("class", "child")

    assert engine._matches(child2, engine.rules[0]) is False

    destroy(child)
    destroy(parent)
    destroy(child2)
    destroy(fake_parent)


def test_matching_rules_cache_respects_ancestry(app: QApplication) -> None:
    """
    Widgets with same class but different ancestors must NOT share a rule-cache entry.

    Regression: the old cache key was (type, objectName, class) which ignored the
    widget's ancestor chain.  Two .child widgets — one inside .parent-a and one inside
    .parent-b — would share a cache entry, so whichever was evaluated first would
    determine the cached result for both, causing the other to get wrong rules.
    """
    engine = make_engine("""
        .child { background-color: blue; }
        .parent-a .child { background-color: red; transition: background-color 300ms; }
    """)

    parent_a = QWidget()
    parent_a.setProperty("class", "parent-a")
    child_a = QWidget(parent_a)
    child_a.setProperty("class", "child")

    parent_b = QWidget()
    parent_b.setProperty("class", "parent-b")
    child_b = QWidget(parent_b)
    child_b.setProperty("class", "child")

    # child_a must match the ancestor-dependent transition rule
    rules_a = engine._matching_rules(child_a)
    assert any(r.transitions for r in rules_a), "child_a (under parent-a) should match the transition rule"

    # child_b must NOT — evaluated AFTER child_a to trigger the cache-sharing bug if present
    rules_b = engine._matching_rules(child_b)
    assert not any(r.transitions for r in rules_b), "child_b (under parent-b) must not share child_a's cache entry"

    # Also verify animation behaviour is correct
    hover_widget(engine, child_a)
    assert _has_anim(engine, child_a, "background-color"), "child_a should animate (ancestor rule matches)"

    hover_widget(engine, child_b)
    assert not _has_anim(engine, child_b, "background-color"), "child_b must not animate (ancestor rule does not match)"

    destroy(child_a)
    destroy(parent_a)
    destroy(child_b)
    destroy(parent_b)


def test_transition_engine_reload_rules(app: QApplication) -> None:
    engine = make_engine(
        ".box { background-color: red; } .box:hover { background-color: blue; transition: background-color 300ms; }"
    )
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    assert id(widget) in engine._connected_widgets

    # Reload rules
    _, new_rules = extract_rules(".box { background-color: blue; }")
    engine.reload_rules(new_rules)

    # _connected_widgets must NOT be cleared: the destroyed-signal is still connected.
    # Clearing without disconnecting would cause a double-connect on the next animation cycle.
    assert id(widget) in engine._connected_widgets
    assert not any(ctx.active_animations for ctx in engine._contexts.values())

    app.processEvents()  # Process the delayed timers
    destroy(widget)


def test_transition_all(app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: red; padding: 10px; }
        .box:hover { background-color: blue; padding: 20px; transition: all 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    # should have anims for background-color, padding-top, right, bottom, left
    assert _has_anim(engine, widget, "background-color")
    assert _has_anim(engine, widget, "padding-top")

    destroy(widget)


# ---------------------------------------------------------------------------
# cubic-bezier: regex extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "easing, expected",
    [
        ("cubic-bezier(0.4, 0, 0.2, 1)", (0.4, 0.0, 0.2, 1.0)),
        ("cubic-bezier(0.25, 0.1, 0.25, 1.0)", (0.25, 0.1, 0.25, 1.0)),
        ("cubic-bezier(0, 0, 1, 1)", (0.0, 0.0, 1.0, 1.0)),
        ("cubic-bezier(0.34, 1.56, 0.64, 1)", (0.34, 1.56, 0.64, 1.0)),  # y > 1 overshoot
        ("cubic-bezier( 0.4 , 0 , 0.2 , 1 )", (0.4, 0.0, 0.2, 1.0)),  # extra spaces
        ("CUBIC-BEZIER(0.4, 0, 0.2, 1)", (0.4, 0.0, 0.2, 1.0)),  # uppercase
    ],
)
def test_cubic_bezier_regex_extracts_values(easing: str, expected: tuple[float, float, float, float]) -> None:
    m = _CUBIC_BEZIER_RE.match(easing)
    assert m is not None
    assert (float(m[1]), float(m[2]), float(m[3]), float(m[4])) == pytest.approx(expected)


@pytest.mark.parametrize(
    "easing",
    ["ease", "ease-in", "linear", "ease-in-out", "cubic_bezier(0.4, 0, 0.2, 1)", ""],
)
def test_cubic_bezier_regex_does_not_match_non_cubic(easing: str) -> None:
    assert _CUBIC_BEZIER_RE.match(easing) is None


# ---------------------------------------------------------------------------
# cubic-bezier: curve shape
# ---------------------------------------------------------------------------


def test_make_cubic_bezier_curve_returns_bezier_spline_type() -> None:
    curve = make_cubic_bezier_curve(0.4, 0.0, 0.2, 1.0)
    assert curve.type() == QEasingCurve.Type.BezierSpline


def test_make_cubic_bezier_curve_boundary_values() -> None:
    curve = make_cubic_bezier_curve(0.4, 0.0, 0.2, 1.0)
    assert curve.valueForProgress(0.0) == pytest.approx(0.0, abs=1e-6)
    assert curve.valueForProgress(1.0) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Hot-reload: reload_rules correctness
# ---------------------------------------------------------------------------


def test_reload_clears_snap_only_css_anim_props(app: QApplication) -> None:
    """
    Snap-only widgets (zero-duration transitions) write to _css_anim_props but create no
    Animation object, so they're absent from active_animations.  reload_rules must still
    clear their stale inline style — otherwise the old value permanently overrides the new
    app stylesheet when the new rules remove the transition entirely.
    """
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    # Hover → zero-duration snap → _css_anim_props populated but NO Animation object.
    hover_widget(engine, widget)
    assert engine._ctx(widget).css_anim_props.get("background-color") is not None
    assert not _anims(engine, widget)

    # Reload with transitions removed entirely.
    _, new_rules = extract_rules(".box { background-color: green; }")
    engine.reload_rules(new_rules)

    # Stale inline style must be gone — without the fix, _css_anim_props still has "blue".
    assert not engine._ctx(widget).css_anim_props

    app.processEvents()
    destroy(widget)


def test_reload_does_not_double_connect_destroyed_signal(app: QApplication) -> None:
    """
    reload_rules must NOT clear _connected_widgets without disconnecting signals.
    If it does, the next animation cycle reconnects a second destroyed callback;
    with N reloads the widget accumulates N+1 callbacks.  The observable invariant
    is that after reload + re-animation + destroy, _on_widget_destroyed logic runs
    cleanly (no double pop, no leftover keys).
    """
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    # First animation cycle — registers destroyed signal.
    hover_widget(engine, widget)
    assert id(widget) in engine._connected_widgets

    # Reload — _connected_widgets must NOT be cleared (signal is still live).
    _, new_rules = extract_rules("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    engine.reload_rules(new_rules)
    assert id(widget) in engine._connected_widgets, (
        "_connected_widgets was cleared; next _connect_destroyed call will double-connect destroyed"
    )

    # Re-animate after reload.
    hover_widget(engine, widget)
    assert id(widget) in engine._connected_widgets

    # Destroy — must not raise and must leave no stale engine state.
    destroy(widget)
    assert not _anims(engine, widget)


def test_reload_hover_animations_resume_after_reload(app: QApplication) -> None:
    """After reload_rules the engine must still create animations on the next hover."""
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)
    assert _has_anim(engine, widget, "background-color")

    _, new_rules = extract_rules("""
        .box { background-color: green; }
        .box:hover { background-color: yellow; transition: background-color 400ms; }
    """)
    engine.reload_rules(new_rules)
    assert not _anims(engine, widget)

    # Hover again after reload — must create a fresh animation with new rules.
    hover_widget(engine, widget)
    assert _has_anim(engine, widget, "background-color")

    destroy(widget)


def test_evaluation_cause_polish_snaps_transitions(app: QApplication) -> None:
    """Polish-triggered evaluations should still snap instead of starting animations."""
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    ctx = engine._ctx(widget)
    ctx.active_pseudos = {":hover"}
    ctx.css_anim_props["background-color"] = "red"

    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)

    assert not _has_anim(engine, widget, "background-color")
    assert engine._ctx(widget).css_anim_props.get("background-color") is not None
    destroy(widget)


def test_class_change_finish_reevaluates_with_explicit_cause(
    app: QApplication, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Class-change completion should re-enter evaluation with CLASS_ANIMATION_FINISH."""
    engine = make_engine("""
        .box { background-color: red; }
        .box.on { background-color: blue; transition: background-color 30ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box on")

    engine._evaluate_widget_state(widget, cause=EvaluationCause.CLASS_CHANGE)

    anim_obj = _get_anim(engine, widget, "background-color")
    assert isinstance(anim_obj, ColorAnimation)
    assert "background-color" in engine._ctx(widget).class_anim_props

    causes: list[EvaluationCause] = []

    def record_evaluation(_widget: QWidget, cause: EvaluationCause = EvaluationCause.DIRECT) -> None:
        causes.append(cause)

    monkeypatch.setattr(engine, "_evaluate_widget_state", record_evaluation)

    qtbot.wait(80)

    assert EvaluationCause.CLASS_ANIMATION_FINISH in causes
    destroy(widget)


def test_reload_multiple_times_state_remains_consistent(app: QApplication) -> None:
    """
    Multiple rapid reloads must leave active_animations empty
    (no state leaks from intermediate reload passes).
    """
    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    css = ".box { background-color: red; } .box:hover { background-color: blue; transition: background-color 300ms; }"
    for _ in range(5):
        _, new_rules = extract_rules(css)
        engine.reload_rules(new_rules)

    assert not any(ctx.active_animations for ctx in engine._contexts.values())
    # _active_widget_ids consolidated into contexts

    app.processEvents()
    destroy(widget)


def test_make_cubic_bezier_curve_linear_is_identity() -> None:
    """cubic-bezier(0, 0, 1, 1) is the linear timing function."""
    curve = make_cubic_bezier_curve(0.0, 0.0, 1.0, 1.0)
    for p in (0.25, 0.5, 0.75):
        assert curve.valueForProgress(p) == pytest.approx(p, abs=0.01)


def test_make_cubic_bezier_curve_ease_in_is_slow_start() -> None:
    """For ease-in cubic-bezier(0.42, 0, 1, 1): at t=0.5, progress < 0.5."""
    curve = make_cubic_bezier_curve(0.42, 0.0, 1.0, 1.0)
    assert curve.valueForProgress(0.5) < 0.5


def test_make_cubic_bezier_curve_ease_out_is_fast_start() -> None:
    """For ease-out cubic-bezier(0, 0, 0.58, 1): at t=0.5, progress > 0.5."""
    curve = make_cubic_bezier_curve(0.0, 0.0, 0.58, 1.0)
    assert curve.valueForProgress(0.5) > 0.5


# ---------------------------------------------------------------------------
# steps(): curve shape
# ---------------------------------------------------------------------------


def test_make_steps_curve_returns_custom_type() -> None:
    curve = make_steps_curve(4, "end")
    assert curve.type() == QEasingCurve.Type.Custom


def test_steps_regex_matches_basic() -> None:
    assert _STEPS_RE.match("steps(4)")
    assert _STEPS_RE.match("steps(3, jump-start)")
    assert _STEPS_RE.match("steps(5, jump-end)")
    assert _STEPS_RE.match("steps(2, jump-none)")
    assert _STEPS_RE.match("steps(6, jump-both)")
    assert _STEPS_RE.match("steps(1, start)")
    assert _STEPS_RE.match("steps(1, end)")


def test_make_steps_jump_end_boundary_values() -> None:
    curve = make_steps_curve(4, "end")
    assert curve.valueForProgress(0.0) == pytest.approx(0.0)
    assert curve.valueForProgress(1.0) == pytest.approx(1.0)


def test_make_steps_jump_end_discrete_steps() -> None:
    curve = make_steps_curve(4, "jump-end")
    assert curve.valueForProgress(0.0) == pytest.approx(0.0)
    assert curve.valueForProgress(0.24) == pytest.approx(0.0)
    assert curve.valueForProgress(0.25) == pytest.approx(0.25)
    assert curve.valueForProgress(0.49) == pytest.approx(0.25)
    assert curve.valueForProgress(0.5) == pytest.approx(0.5)
    assert curve.valueForProgress(0.74) == pytest.approx(0.5)
    assert curve.valueForProgress(0.75) == pytest.approx(0.75)
    assert curve.valueForProgress(0.99) == pytest.approx(0.75)
    assert curve.valueForProgress(1.0) == pytest.approx(1.0)


def test_make_steps_jump_start_discrete_steps() -> None:
    curve = make_steps_curve(4, "jump-start")
    assert curve.valueForProgress(0.0) == pytest.approx(0.25)
    assert curve.valueForProgress(0.24) == pytest.approx(0.25)
    assert curve.valueForProgress(0.25) == pytest.approx(0.5)
    assert curve.valueForProgress(0.5) == pytest.approx(0.75)
    assert curve.valueForProgress(0.75) == pytest.approx(1.0)
    assert curve.valueForProgress(1.0) == pytest.approx(1.0)


def test_make_steps_jump_none_boundary_values() -> None:
    curve = make_steps_curve(4, "jump-none")
    assert curve.valueForProgress(0.0) == pytest.approx(0.0)
    assert curve.valueForProgress(1.0) == pytest.approx(1.0)


def test_make_steps_jump_none_discrete_steps() -> None:
    curve = make_steps_curve(4, "jump-none")
    # 4 steps, 4 output levels: 0, 1/3, 2/3, 1
    assert curve.valueForProgress(0.0) == pytest.approx(0.0)
    assert curve.valueForProgress(0.24) == pytest.approx(0.0)
    assert curve.valueForProgress(0.25) == pytest.approx(1 / 3)
    assert curve.valueForProgress(0.5) == pytest.approx(2 / 3)
    assert curve.valueForProgress(0.75) == pytest.approx(1.0)


def test_make_steps_jump_both_boundary_values() -> None:
    curve = make_steps_curve(4, "jump-both")
    assert curve.valueForProgress(0.0) == pytest.approx(1 / 5)
    assert curve.valueForProgress(1.0) == pytest.approx(1.0)


def test_step_start_alias_same_as_jump_start_1() -> None:
    c1 = make_steps_curve(1, "start")
    c2 = make_steps_curve(1, "jump-start")
    for t in (0.0, 0.5, 1.0):
        assert c1.valueForProgress(t) == pytest.approx(c2.valueForProgress(t))


def test_step_end_alias_same_as_jump_end_1() -> None:
    c1 = make_steps_curve(1, "end")
    c2 = make_steps_curve(1, "jump-end")
    for t in (0.0, 0.5, 1.0):
        assert c1.valueForProgress(t) == pytest.approx(c2.valueForProgress(t))


# ---------------------------------------------------------------------------
# opacity + box-shadow coexistence
# ---------------------------------------------------------------------------


def test_shadow_stored_when_opacity_holds_slot(app: QApplication) -> None:
    """apply_shadow_to_widget stores _desired_shadow even when opacity blocks install."""
    widget = QWidget()
    params = ShadowParams(0, 0, 10, 0, QColor("blue"))

    # Install opacity first
    apply_opacity_to_widget(widget, 0.5, "opacity")
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    # Shadow blocked, but desired stored
    apply_shadow_to_widget(widget, params, "opacity")
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)
    assert getattr(widget, "_desired_shadow", None) is params

    destroy(widget)


def test_shadow_restored_when_opacity_reaches_one(app: QApplication) -> None:
    """Shadow is installed when opacity animation completes at 1.0."""
    widget = QWidget()
    params = ShadowParams(0, 0, 10, 0, QColor("blue"))

    apply_opacity_to_widget(widget, 0.5, "opacity")
    apply_shadow_to_widget(widget, params, "opacity")

    # Opacity reaches 1.0 → opacity effect removed, shadow installed
    apply_opacity_to_widget(widget, 1.0, "opacity")
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.blurRadius() == 10.0

    destroy(widget)


def test_shadow_hidden_when_opacity_takes_over_from_full(app: QApplication) -> None:
    """When opacity starts animating down from 1.0, shadow is replaced with opacity effect."""
    widget = QWidget()
    params = ShadowParams(0, 0, 10, 0, QColor("blue"))

    # Start at opacity=1.0 with shadow showing
    apply_shadow_to_widget(widget, params, "opacity")
    assert isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect)

    # Opacity starts animating down
    apply_opacity_to_widget(widget, 0.8, "opacity")
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    destroy(widget)


def test_no_shadow_on_opacity_one_without_desired(app: QApplication) -> None:
    """No shadow effect installed if _desired_shadow not set when opacity reaches 1.0."""
    widget = QWidget()
    apply_opacity_to_widget(widget, 0.5, "opacity")
    apply_opacity_to_widget(widget, 1.0, "opacity")
    assert widget.graphicsEffect() is None
    destroy(widget)


def test_shadow_desired_none_clears_shadow(app: QApplication) -> None:
    """apply_shadow_to_widget(None) removes shadow and sets _desired_shadow to None."""
    widget = QWidget()
    params = ShadowParams(0, 0, 10, 0, QColor("blue"))

    apply_shadow_to_widget(widget, params, "opacity")
    assert isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect)

    apply_shadow_to_widget(widget, None, "opacity")
    assert widget.graphicsEffect() is None
    assert getattr(widget, "_desired_shadow", "unset") is None

    destroy(widget)


def test_shadow_desired_none_prevents_restore_on_opacity_one(app: QApplication) -> None:
    """If shadow is cleared while opacity active, reaching opacity=1.0 does not restore it."""
    widget = QWidget()
    params = ShadowParams(0, 0, 10, 0, QColor("blue"))

    apply_opacity_to_widget(widget, 0.5, "opacity")
    apply_shadow_to_widget(widget, params, "opacity")  # desired set
    apply_shadow_to_widget(widget, None, "opacity")  # desired cleared

    apply_opacity_to_widget(widget, 1.0, "opacity")
    assert widget.graphicsEffect() is None

    destroy(widget)


# ---------------------------------------------------------------------------
# transition-delay
# ---------------------------------------------------------------------------


def test_delay_schedules_pending_timer(app: QApplication) -> None:
    """With delay_ms > 0, hovering should create a pending timer, not an active animation."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 100ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert not _has_anim(engine, widget, "background-color"), "animation must not start until delay fires"
    assert "background-color" in ctx.pending_delays, "pending timer must be scheduled"

    ctx.pending_delays["background-color"].stop()
    destroy(widget)


def test_delay_fires_animation_after_elapsed(app: QApplication, qtbot: QtBot) -> None:
    """After the delay elapses, the animation object should be created."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 50ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)
    assert not _has_anim(engine, widget, "background-color")

    qtbot.wait(120)  # > 50ms delay

    assert _has_anim(engine, widget, "background-color"), "animation must start after delay"
    destroy(widget)


def test_delay_cancelled_on_state_change(app: QApplication) -> None:
    """If the widget unhovers before the delay fires, the pending timer is cancelled."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)
    ctx = engine._ctx(widget)
    assert "background-color" in ctx.pending_delays

    # Unhover before delay fires
    ctx.active_pseudos = set()
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    assert "background-color" not in ctx.pending_delays, "timer must be cancelled on state change"
    assert not _has_anim(engine, widget, "background-color")

    destroy(widget)


def test_delay_widget_destroyed_no_crash(app: QApplication) -> None:
    """Widget destroyed while delay pending must not raise."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)
    assert "background-color" in engine._ctx(widget).pending_delays

    destroy(widget)  # must not raise; _on_widget_destroyed cancels timer

    assert id(widget) not in engine._contexts


def test_delay_applies_on_second_hover_cycle(app: QApplication, qtbot: QtBot) -> None:
    """Delay must fire on every hover, not just the first — anim_obj persists after finish."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 50ms ease 80ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    # First cycle: hover → wait for delay + anim → unhover
    hover_widget(engine, widget)
    qtbot.wait(200)  # delay(80) + anim(50) + margin
    engine._ctx(widget).active_pseudos = set()
    engine._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)
    qtbot.wait(200)

    # Second cycle: hover again — delay must apply again
    hover_widget(engine, widget)
    ctx = engine._ctx(widget)
    assert "background-color" in ctx.pending_delays, "delay must be re-scheduled on second hover"
    assert ctx.active_animations["background-color"].anim.state() != QAbstractAnimation.State.Running

    ctx.pending_delays["background-color"].stop()
    destroy(widget)


def test_delay_freezes_current_value_during_delay(app: QApplication) -> None:
    """
    During the delay period, the current value must be frozen in css_anim_props so Qt's
    new class/state stylesheet value doesn't apply immediately (no visual jump).
    """
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert "background-color" in ctx.pending_delays, "timer must be scheduled"
    # Frozen value must be written so inline style overrides the new state's QSS color
    assert "background-color" in ctx.css_anim_props, "current value must be frozen in css_anim_props"

    ctx.pending_delays["background-color"].stop()
    destroy(widget)


def test_delay_class_change_freezes_size_prevents_jump(app: QApplication) -> None:
    """Class-change with delayed size transition: size must be frozen during delay, not jump to target."""
    engine = make_engine("""
        .box { min-width: 50px; max-width: 50px; transition: all 200ms 500ms ease; }
        .box.active { min-width: 150px; max-width: 150px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.setMinimumWidth(50)
    widget.setMaximumWidth(50)

    engine._on_class_change(widget)  # simulate class change to .box.active after setting class
    widget.setProperty("class", "box active")
    engine._on_class_change(widget)

    ctx = engine._ctx(widget)
    # Frozen value must hold the widget at the pre-change size, not the new 150px
    assert "min-width" in ctx.css_anim_props, "min-width must be frozen during delay"
    assert "min-width" in ctx.pending_delays, "delay timer must be scheduled"

    for t in list(ctx.pending_delays.values()):
        t.stop()
    destroy(widget)


def test_delay_zero_no_pending_timer(app: QApplication) -> None:
    """With delay_ms == 0, no pending timer is created — animation starts immediately."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")

    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert not ctx.pending_delays, "no timer scheduled for zero delay"
    assert _has_anim(engine, widget, "background-color"), "animation starts immediately"

    destroy(widget)


def test_negative_delay_starts_immediately(app: QApplication) -> None:
    """Negative delay: no pending timer, animation object created right away."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 500ms ease -100ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert not ctx.pending_delays, "no timer scheduled for negative delay"
    assert _has_anim(engine, widget, "background-color"), "animation must start immediately"

    destroy(widget)


def test_negative_delay_seeks_into_animation(app: QApplication) -> None:
    """Negative delay: animation currentTime == |delay|, not 0."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 500ms ease -200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    anim_obj = _get_anim(engine, widget, "background-color")
    assert isinstance(anim_obj, ColorAnimation)
    assert anim_obj.anim.currentTime() >= 200, "animation must be seeked 200ms into timeline"

    destroy(widget)


def test_negative_delay_exceeds_duration_snaps(app: QApplication) -> None:
    """Negative delay >= duration: animation finishes immediately (snaps to target)."""
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease -500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)

    # Animation may finish and clean up, or be in Stopped state at end value.
    anim_obj = _anims(engine, widget).get("background-color")
    if anim_obj is not None:
        assert anim_obj.anim.state() != QAbstractAnimation.State.Running, "animation must not be running"

    destroy(widget)


# ---------------------------------------------------------------------------
# cursor property
# ---------------------------------------------------------------------------


def test_cursor_has_cursor_rules_flag(app: QApplication) -> None:
    engine = make_engine(".btn { cursor: pointer; }")
    assert engine._has_cursor_rules is True


def test_cursor_no_cursor_rules_flag(app: QApplication) -> None:
    engine = make_engine(".btn { background-color: steelblue; }")
    assert engine._has_cursor_rules is False


def test_cursor_applied_on_hover(app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { cursor: pointer; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    hover_widget(engine, widget)

    ctx = engine._ctx(widget)
    assert ctx.applied_cursor == "pointer"
    assert widget.cursor().shape() == Qt.CursorShape.PointingHandCursor

    destroy(widget)


def test_cursor_unset_on_hover_leave(app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { cursor: pointer; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    hover_widget(engine, widget)
    # leave hover
    engine._ctx(widget).active_pseudos = set()
    engine._evaluate_widget_state(widget)

    ctx = engine._ctx(widget)
    assert ctx.applied_cursor is None

    destroy(widget)


def test_cursor_base_state_applied_on_evaluate(app: QApplication) -> None:

    engine = make_engine(".btn { cursor: default; }")
    widget = QWidget()
    widget.setProperty("class", "btn")

    engine._evaluate_widget_state(widget)

    ctx = engine._ctx(widget)
    assert ctx.applied_cursor == "default"
    assert widget.cursor().shape() == Qt.CursorShape.ArrowCursor

    destroy(widget)


def test_cursor_idempotent(app: QApplication) -> None:
    """Re-evaluating with unchanged cursor must not call setCursor again."""
    engine = make_engine(".btn { cursor: pointer; }")
    widget = QWidget()
    widget.setProperty("class", "btn")

    engine._evaluate_widget_state(widget)
    ctx = engine._ctx(widget)
    assert ctx.applied_cursor == "pointer"

    # Override cursor manually then re-evaluate — applied_cursor unchanged, so no setCursor.
    widget.setCursor(Qt.CursorShape.ArrowCursor)
    engine._evaluate_widget_state(widget)
    # applied_cursor still "pointer" → engine skipped the call → widget still has ArrowCursor
    assert ctx.applied_cursor == "pointer"
    assert widget.cursor().shape() == Qt.CursorShape.ArrowCursor

    destroy(widget)


# ---------------------------------------------------------------------------
# Window deactivate: clears stuck :hover / :pressed from children
# ---------------------------------------------------------------------------


def test_window_deactivate_clears_stuck_hover(app: QApplication) -> None:
    """_on_window_deactivate must clear :hover from children and snap animations back."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 1000ms; }
    """)
    parent = QWidget()
    child = QWidget(parent)
    child.setProperty("class", "btn")

    hover_widget(engine, child)
    assert ":hover" in engine._ctx(child).active_pseudos
    assert _has_anim(engine, child, "background-color")

    engine._on_window_deactivate(parent)

    assert ":hover" not in engine._ctx(child).active_pseudos

    destroy(child)
    destroy(parent)


def test_window_deactivate_clears_stuck_pressed(app: QApplication) -> None:
    """_on_window_deactivate must clear :pressed from children."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:pressed { background-color: black; transition: background-color 300ms; }
    """)
    parent = QWidget()
    child = QWidget(parent)
    child.setProperty("class", "btn")

    engine._ctx(child).active_pseudos = {":pressed"}
    engine._evaluate_widget_state(child)
    assert ":pressed" in engine._ctx(child).active_pseudos

    engine._on_window_deactivate(parent)

    assert ":pressed" not in engine._ctx(child).active_pseudos

    destroy(child)
    destroy(parent)


def test_window_deactivate_does_not_clear_focus(app: QApplication) -> None:
    """:focus is not transient — _on_window_deactivate must leave it alone."""
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:focus { background-color: royalblue; transition: background-color 300ms; }
    """)
    parent = QWidget()
    child = QWidget(parent)
    child.setProperty("class", "btn")

    engine._ctx(child).active_pseudos = {":focus"}
    engine._evaluate_widget_state(child)

    engine._on_window_deactivate(parent)

    assert ":focus" in engine._ctx(child).active_pseudos

    destroy(child)
    destroy(parent)


# ---------------------------------------------------------------------------
# Checkable button: :checked pseudo-state integration
# ---------------------------------------------------------------------------


def test_checkable_button_initial_checked_state_synced(app: QApplication) -> None:
    """A checkable button that starts checked must have :checked in active_pseudos after _connect_checkable."""
    engine = make_engine("""
        .toggle { background-color: steelblue; }
        .toggle:checked { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QCheckBox()
    widget.setProperty("class", "toggle")
    widget.setChecked(True)

    engine._connect_checkable(widget)

    assert ":checked" in engine._ctx(widget).active_pseudos

    destroy(widget)


def test_checkable_button_unchecked_no_checked_pseudo(app: QApplication) -> None:
    """A checkable button that starts unchecked must NOT have :checked in active_pseudos."""
    engine = make_engine("""
        .toggle { background-color: steelblue; }
        .toggle:checked { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QCheckBox()
    widget.setProperty("class", "toggle")
    widget.setChecked(False)

    engine._connect_checkable(widget)

    assert ":checked" not in engine._ctx(widget).active_pseudos

    destroy(widget)


def test_checkable_button_toggle_adds_checked_pseudo(app: QApplication) -> None:
    """Toggling a checkable button on must add :checked and trigger re-evaluation."""
    engine = make_engine("""
        .toggle { background-color: steelblue; }
        .toggle:checked { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QCheckBox()
    widget.setProperty("class", "toggle")
    widget.setChecked(False)

    engine._connect_checkable(widget)
    engine._on_checked_changed(widget, True)

    assert ":checked" in engine._ctx(widget).active_pseudos
    assert _has_anim(engine, widget, "background-color")

    destroy(widget)


def test_checkable_button_toggle_off_removes_checked_pseudo(app: QApplication) -> None:
    """Toggling a checkable button off must discard :checked."""
    engine = make_engine("""
        .toggle { background-color: steelblue; }
        .toggle:checked { background-color: royalblue; transition: background-color 300ms; }
    """)
    widget = QCheckBox()
    widget.setProperty("class", "toggle")

    engine._connect_checkable(widget)
    engine._on_checked_changed(widget, True)
    assert ":checked" in engine._ctx(widget).active_pseudos

    engine._on_checked_changed(widget, False)
    assert ":checked" not in engine._ctx(widget).active_pseudos

    destroy(widget)


def test_connect_checkable_idempotent(app: QApplication) -> None:
    """Calling _connect_checkable multiple times must not double-connect the toggled signal."""
    engine = make_engine(".toggle { background-color: steelblue; }")
    widget = QCheckBox()
    widget.setProperty("class", "toggle")
    widget.setChecked(False)

    engine._connect_checkable(widget)
    engine._connect_checkable(widget)
    engine._connect_checkable(widget)

    # If double-connected, toggling would call _on_checked_changed twice → check for idempotence.
    engine._ctx(widget).active_pseudos.discard(":checked")
    widget.setChecked(True)
    app.processEvents()

    # :checked added exactly once
    assert ":checked" in engine._ctx(widget).active_pseudos

    destroy(widget)


# ---------------------------------------------------------------------------
# GenericPropertyAnimation: non-negative clamping for overshoot
# ---------------------------------------------------------------------------


def test_generic_animation_negative_clamped_in_stylesheet(app: QApplication) -> None:
    """
    Cubic-bezier overshoot can drive current_val negative for non-negative props.
    The stylesheet value must be clamped to 0 while current_val stays unclamped.
    """
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "padding-top", 10.0, 100, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("5px")

    # Simulate overshoot: the animation value goes below 0
    anim._on_tick(-3.0)

    assert anim.current_val == -3.0, "current_val must stay unclamped"
    stored = ctx.css_anim_props.get("padding-top", "")
    assert stored.startswith("0.000px"), f"expected clamped 0, got {stored!r}"

    destroy(widget)


def test_generic_animation_non_negative_prop_zero_boundary(app: QApplication) -> None:
    """Exactly 0 is allowed for non-negative props (boundary check)."""
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "border-top-width", 5.0, 100, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("0px")

    anim._on_tick(0.0)

    stored = ctx.css_anim_props.get("border-top-width", "")
    assert stored == "0.000px", f"expected 0.000px, got {stored!r}"

    destroy(widget)


def test_generic_animation_margin_allows_negative(app: QApplication) -> None:
    """margin-top is NOT in _NON_NEGATIVE_PROPS — negative values must pass through."""
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "margin-top", 10.0, 100, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("0px")

    anim._on_tick(-5.0)

    stored = ctx.css_anim_props.get("margin-top", "")
    assert stored == "-5.000px", f"expected -5.000px, got {stored!r}"

    destroy(widget)


# ---------------------------------------------------------------------------
# update_spec: changing duration / easing on live animation objects
# ---------------------------------------------------------------------------


def test_color_animation_set_target_skips_restart_when_already_running_to_same_target(app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetContext()
    anim = ColorAnimation(widget, "background-color", "red", 500, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("blue")
    assert anim.anim.state() == anim.anim.State.Running

    # Advance mid-flight so current_color != start_color.
    anim._on_tick(0.5)
    mid_color = anim.current_color

    # Re-target to the same end color — must not restart (no jump back to red).
    anim.set_target("blue")
    assert anim.anim.state() == anim.anim.State.Running
    assert anim.current_color == mid_color

    destroy(widget)


def test_generic_animation_set_target_skips_restart_when_already_running_to_same_target(app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "width", 10.0, 500, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("200px")
    assert anim.anim.state() == anim.anim.State.Running

    # Advance mid-flight so current_val != start val.
    anim._on_tick(100.0)
    mid_val = anim.current_val

    # Re-target to same end value — must not restart (no jump back to 10.0).
    anim.set_target("200px")
    assert anim.anim.state() == anim.anim.State.Running
    assert anim.current_val == mid_val

    destroy(widget)


def test_update_spec_changes_duration_on_color_animation(app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetContext()
    anim = ColorAnimation(widget, "background-color", "steelblue", 1000, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("royalblue")
    assert anim.anim.duration() == 1000

    anim.update_spec(500, QEasingCurve(QEasingCurve.Type.InCubic))
    assert anim.anim.duration() == 500

    destroy(widget)


def test_update_spec_changes_duration_on_generic_animation(app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "padding-top", 10.0, 800, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("20px")
    assert anim.anim.duration() == 800

    anim.update_spec(200, QEasingCurve(QEasingCurve.Type.OutCubic))
    assert anim.anim.duration() == 200

    destroy(widget)


def test_update_spec_changes_easing_curve(app: QApplication) -> None:
    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "padding-top", 0.0, 300, QEasingCurve.Type.Linear, ctx=ctx)
    assert anim.anim.easingCurve().type() == QEasingCurve.Type.Linear

    anim.update_spec(300, QEasingCurve(QEasingCurve.Type.InOutCubic))
    assert anim.anim.easingCurve().type() == QEasingCurve.Type.InOutCubic

    destroy(widget)


# ---------------------------------------------------------------------------
# :clicked pseudo-class — engine
# ---------------------------------------------------------------------------


def _click_widget(engine: TransitionEngine, widget: QWidget) -> EvaluationCause:
    """Simulate a mouse press through the full _prepare_clicked → evaluate path."""
    ctx = engine._ctx(widget)
    updated = engine._update_pseudos(ctx.active_pseudos, QEvent.Type.MouseButtonPress)
    cause = engine._prepare_clicked(widget, ctx, updated)
    if updated != ctx.active_pseudos:
        ctx.active_pseudos = updated
        engine._evaluate_widget_state(widget, cause=cause)
        if cause is EvaluationCause.CLICKED_ACTIVATION:
            engine._finish_clicked_activation(widget, ctx)
    return cause


def test_clicked_prepare_returns_activated_cause(app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    cause = _click_widget(engine, widget)
    assert cause is EvaluationCause.CLICKED_ACTIVATION
    destroy(widget)


def test_clicked_prepare_returns_pseudo_state_when_no_rules(app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:hover { background-color: green; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    cause = _click_widget(engine, widget)
    assert cause is EvaluationCause.PSEUDO_STATE
    destroy(widget)


def test_clicked_added_to_active_pseudos(app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    assert ":clicked" in engine._ctx(widget).active_pseudos
    destroy(widget)


def test_clicked_animation_starts_toward_clicked_target(app: QApplication) -> None:
    """Forward animation must target the :clicked value, not the base value."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 500ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)

    assert _has_anim(engine, widget, "background-color")
    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    assert anim.anim.state() == QAbstractAnimation.State.Running
    # End color must be red (#ff0000)
    assert anim.end_color.red() > 200
    assert anim.end_color.green() < 50
    assert anim.end_color.blue() < 50
    destroy(widget)


def test_clicked_priority_beats_pressed(app: QApplication) -> None:
    """:clicked (priority 3) must win over :pressed (priority 2) when both active."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 500ms; }
        .btn:pressed { background-color: green; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)  # adds both :pressed and :clicked

    ctx = engine._ctx(widget)
    assert ":pressed" in ctx.active_pseudos
    assert ":clicked" in ctx.active_pseudos

    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)
    # Target must be red (:clicked) not green (:pressed)
    assert anim.end_color.red() > 200
    assert anim.end_color.green() < 50
    destroy(widget)


def test_clicked_anim_props_pre_populated(app: QApplication) -> None:
    """`clicked_anim_props` must contain the props declared in the :clicked rule."""
    engine = make_engine("""
        .btn { background-color: blue; color: white; transition: background-color 400ms, color 400ms; }
        .btn:clicked { background-color: red; color: black; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    ctx = engine._ctx(widget)
    updated = engine._update_pseudos(ctx.active_pseudos, QEvent.Type.MouseButtonPress)
    engine._prepare_clicked(widget, ctx, updated)

    assert "background-color" in ctx.clicked_anim_props
    assert "color" in ctx.clicked_anim_props
    destroy(widget)


def test_clicked_reignition_ignored_during_forward_animation(app: QApplication) -> None:
    """Re-clicking while :clicked is active must not restart or increment the gen counter."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 1000ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    ctx = engine._ctx(widget)
    gen_before = ctx.clicked_anim_gen
    props_before = set(ctx.clicked_anim_props)

    # Second click while animation still running
    cause2 = _click_widget(engine, widget)

    assert cause2 is EvaluationCause.PSEUDO_STATE  # not CLICKED_ACTIVATION
    assert ctx.clicked_anim_gen == gen_before  # gen unchanged
    assert ctx.clicked_anim_props == props_before  # set unchanged
    destroy(widget)


def test_clicked_snap_deactivates_on_next_tick(app: QApplication) -> None:
    """When all :clicked props snap (duration=0), :clicked is removed in the next event-loop tick."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 0ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    ctx = engine._ctx(widget)

    # Immediately after click: :clicked still in pseudos (timer not fired yet)
    assert ":clicked" in ctx.active_pseudos

    # After processing pending events (QTimer.singleShot(0) fires): :clicked removed
    QApplication.processEvents()
    assert ":clicked" not in ctx.active_pseudos
    destroy(widget)


def test_clicked_deactivates_after_forward_animation_completes(app: QApplication, qtbot: QtBot) -> None:
    """:clicked must be removed from active_pseudos once the forward animation finishes."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 60ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    assert ":clicked" in engine._ctx(widget).active_pseudos

    qtbot.wait(150)  # > 60ms animation duration

    assert ":clicked" not in engine._ctx(widget).active_pseudos
    destroy(widget)


def test_clicked_reverse_animation_fires_after_deactivation(app: QApplication, qtbot: QtBot) -> None:
    """After :clicked deactivates, a reverse animation toward base must start."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 60ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    # Forward animation running: target = red
    anim = _get_anim(engine, widget, "background-color")
    assert isinstance(anim, ColorAnimation)

    qtbot.wait(150)  # forward anim finishes → :clicked deactivates → reverse starts

    # After deactivation the same animation object is re-targeted toward blue
    anim2 = _get_anim(engine, widget, "background-color")
    assert isinstance(anim2, ColorAnimation)
    assert anim2.end_color.blue() > 150  # target is blue (#0000ff)
    assert anim2.end_color.red() < 50
    destroy(widget)


def test_clicked_destroyed_during_animation_no_crash(app: QApplication) -> None:
    """Deleting the widget while the :clicked forward animation runs must not raise."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 1000ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    assert _has_anim(engine, widget, "background-color")

    destroy(widget)  # must not raise

    assert id(widget) not in engine._contexts


def test_clicked_context_cleaned_up_on_destroy(app: QApplication) -> None:
    """clicked_anim_props and clicked_anim_callbacks must be empty after destroy."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 1000ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    ctx = engine._ctx(widget)
    assert ctx.clicked_anim_props  # non-empty while running

    destroy(widget)

    # Context is gone — no stale state
    assert id(widget) not in engine._contexts


def test_clicked_reload_clears_state(app: QApplication) -> None:
    """reload_rules must discard :clicked from active_pseudos and clear tracking sets."""
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 1000ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")

    _click_widget(engine, widget)
    ctx = engine._ctx(widget)
    assert ":clicked" in ctx.active_pseudos

    _, new_rules = extract_rules("""
        .btn { background-color: blue; transition: background-color 1000ms; }
        .btn:clicked { background-color: red; }
    """)
    engine.reload_rules(new_rules)

    assert ":clicked" not in ctx.active_pseudos
    assert not ctx.clicked_anim_props
    assert not ctx.clicked_anim_callbacks
    destroy(widget)


# ---------------------------------------------------------------------------
# Effect props (opacity / box-shadow) initialization and hot-reload
# ---------------------------------------------------------------------------


def test_effect_opacity_installed_on_initial_evaluation(app: QApplication) -> None:
    """Base-state opacity rule must install QGraphicsOpacityEffect on first evaluation."""
    engine = make_engine(".box { opacity: 0.5; }")
    widget = QWidget()
    widget.setProperty("class", "box")

    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)

    assert _has_anim(engine, widget, "opacity")
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert effect.opacity() == pytest.approx(0.5)
    destroy(widget)


def test_effect_box_shadow_installed_on_initial_evaluation(app: QApplication) -> None:
    """Base-state box-shadow rule must install QGraphicsDropShadowEffect on first evaluation."""
    engine = make_engine(".box { box-shadow: 0 4px 8px rgba(0,0,0,0.5); }")
    widget = QWidget()
    widget.setProperty("class", "box")

    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)

    assert _has_anim(engine, widget, "box-shadow")
    assert isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect)
    destroy(widget)


def test_effect_opacity_not_skipped_when_target_equals_base(app: QApplication) -> None:
    """
    Regression: a no-transition effect prop where target_raw == base_props value must still
    create the QGraphicsEffect.  Earlier code skipped via the css_anim_props short-circuit
    because effect props don't live in css_anim_props.
    """
    engine = make_engine(".box { opacity: 0.7; }")
    widget = QWidget()
    widget.setProperty("class", "box")

    engine._evaluate_widget_state(widget, cause=EvaluationCause.RULE_RELOAD)

    assert _has_anim(engine, widget, "opacity")
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)
    destroy(widget)


def test_reload_reapplies_opacity_effect(app: QApplication, qtbot: QtBot) -> None:
    """After hot-reload, opacity effect must be reinstalled with new value."""
    engine = make_engine(".box { opacity: 0.5; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    app.processEvents()

    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert _has_anim(engine, widget, "opacity")
    first_effect = widget.graphicsEffect()
    assert isinstance(first_effect, QGraphicsOpacityEffect)
    assert first_effect.opacity() == pytest.approx(0.5)

    _, new_rules = extract_rules(".box { opacity: 0.2; }")
    engine.reload_rules(new_rules)
    qtbot.wait(20)  # flush deferred _reeval_effect_widgets_deferred + Polish queue

    assert _has_anim(engine, widget, "opacity")
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert effect.opacity() == pytest.approx(0.2)
    destroy(widget)


def test_reload_reapplies_box_shadow_effect(app: QApplication, qtbot: QtBot) -> None:
    """After hot-reload, box-shadow effect must be reinstalled with new params."""
    engine = make_engine(".box { box-shadow: 0 4px 8px rgba(0,0,0,0.5); }")
    widget = QWidget()
    widget.setProperty("class", "box")
    app.processEvents()

    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert _has_anim(engine, widget, "box-shadow")
    assert isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect)

    _, new_rules = extract_rules(".box { box-shadow: 0 8px 16px rgba(0,0,0,0.8); }")
    engine.reload_rules(new_rules)
    qtbot.wait(20)

    assert _has_anim(engine, widget, "box-shadow")
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.blurRadius() == pytest.approx(16.0)
    destroy(widget)


def test_reload_adds_opacity_to_widget_with_no_prior_effect(app: QApplication, qtbot: QtBot) -> None:
    """Hot-reload that introduces an effect rule for the first time must install the effect."""
    engine = make_engine(".box { background-color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert widget.graphicsEffect() is None

    _, new_rules = extract_rules(".box { background-color: red; opacity: 0.4; }")
    engine.reload_rules(new_rules)
    qtbot.wait(20)

    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert effect.opacity() == pytest.approx(0.4)
    destroy(widget)


def test_reload_removes_opacity_when_rule_dropped(app: QApplication, qtbot: QtBot) -> None:
    """Reload that removes the effect rule must tear down the QGraphicsEffect."""
    engine = make_engine(".box { opacity: 0.3; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    engine._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    _, new_rules = extract_rules(".box { background-color: red; }")
    engine.reload_rules(new_rules)
    qtbot.wait(20)

    assert not _has_anim(engine, widget, "opacity")
    assert widget.graphicsEffect() is None
    destroy(widget)
