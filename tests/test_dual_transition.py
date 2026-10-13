# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""Dual-transition tests: transition defined on both base and target selectors.

CSS semantics: the transition used is the one from the *after-change* state.
Hover-in uses the :hover timing (fast); hover-out falls back to the base
timing (slow). The reverse animation must start from the current visual value
with no jump.

Regression background: the evaluator retimes the live QVariantAnimation
(update_spec) before retargeting it (set_target). Retiming a running or
finished animation makes Qt re-emit valueChanged synchronously at the stale
currentTime with the OLD endpoints — e.g. a finished 150ms hover-in retimed
to 1000ms ticks at progress 0.15 with hover endpoints, corrupting the stored
current value (#417800 became #64818a). set_target then captured the corrupted
value as the reverse start, causing a visible jump. The fix blocks signals
during update_spec in each animator so set_target restarts from the preserved
value.
"""

import pytest

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEasingCurve
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


DUAL_BG_CSS = """
.btn {
  background: #7c80c7;
  transition: background 1000ms ease-out;
}
.btn:hover {
  background: #417800;
  transition: background 150ms ease-in;
}
"""


def test_dual_transition_hover_in_uses_hover_timing(_app: QApplication) -> None:
    engine = make_engine(DUAL_BG_CSS)
    widget = QWidget()
    widget.setProperty("class", "btn")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    anim = ctx.active_animations["background-color"]
    assert isinstance(anim, ColorAnimation)
    assert anim.anim.duration() == 150
    assert anim.anim.easingCurve().type() == QEasingCurve.Type.InCubic
    assert anim.end_color == QColor("#417800")
    destroy(widget)


def test_dual_transition_hover_out_uses_base_timing_without_jump(_app: QApplication) -> None:
    """Reverse must use base timing and start exactly from the hover value.

    Catches the stale-tick bug: retiming the finished 150ms hover-in to the
    1000ms base duration re-emitted valueChanged at progress 0.15 with hover
    endpoints, so the reverse started from a blended midpoint (#64818a)
    instead of the hover color (#417800).
    """
    engine = make_engine(DUAL_BG_CSS)
    widget = QWidget()
    widget.setProperty("class", "btn")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    anim = ctx.active_animations["background-color"]
    assert isinstance(anim, ColorAnimation)
    # Finish hover-in so the widget visually sits on the hover color.
    anim.anim.setCurrentTime(anim.anim.duration())
    assert anim.current_color == QColor("#417800")

    ctx.active_pseudos = set()
    engine.evaluate_widget_state(widget)
    back = ctx.active_animations["background-color"]
    assert isinstance(back, ColorAnimation)
    assert back is anim
    # Slow base timing applies to the way back.
    assert back.anim.duration() == 1000
    assert back.anim.easingCurve().type() == QEasingCurve.Type.OutCubic
    assert back.end_color == QColor("#7c80c7")
    # No jump: reverse starts from the hover color, not a blended midpoint.
    assert back.start_color == QColor("#417800")
    assert back.current_color == QColor("#417800")
    assert QColor(ctx.css_anim_props["background-color"]) == QColor("#417800")
    destroy(widget)


def test_dual_transition_midflight_reverse_starts_from_current(_app: QApplication) -> None:
    """Interrupting hover-in mid-flight must retarget from the visible midpoint.

    Same stale-tick path as above, but with a genuinely running animation:
    the reverse start must equal the on-screen midpoint, not a value shifted
    by the retiming tick.
    """
    engine = make_engine(DUAL_BG_CSS)
    widget = QWidget()
    widget.setProperty("class", "btn")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    anim = ctx.active_animations["background-color"]
    assert isinstance(anim, ColorAnimation)
    anim.anim.setCurrentTime(75)
    midpoint = QColor(anim.current_color)
    assert midpoint != QColor("#7c80c7")
    assert midpoint != QColor("#417800")

    ctx.active_pseudos = set()
    engine.evaluate_widget_state(widget)
    back = ctx.active_animations["background-color"]
    assert isinstance(back, ColorAnimation)
    assert back.anim.duration() == 1000
    assert back.start_color == midpoint
    assert back.current_color == midpoint
    destroy(widget)


def test_dual_transition_numeric_hover_out_without_jump(_app: QApplication) -> None:
    """Same no-jump guarantee for numeric props (padding).

    GenericPropertyAnimation shares the update_spec/set_target pairing, so a
    finished 150ms hover-in retimed to 900ms corrupted current_val the same
    way (padding-top read 13.37 instead of 18.0).
    """
    engine = make_engine("""
.pad {
  padding: 10px 16px;
  transition: padding 900ms ease-out;
}
.pad:hover {
  padding: 18px 30px;
  transition: padding 150ms ease-in;
}
""")
    widget = QWidget()
    widget.setProperty("class", "pad")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    assert ctx.active_animations["padding-top"].anim.duration() == 150
    for side in ("padding-top", "padding-right", "padding-bottom", "padding-left"):
        a = ctx.active_animations[side]
        assert isinstance(a, GenericPropertyAnimation)
        a.anim.setCurrentTime(a.anim.duration())
    top_anim = ctx.active_animations["padding-top"]
    assert isinstance(top_anim, GenericPropertyAnimation)
    assert top_anim.current_val == pytest.approx(18.0)
    right_anim = ctx.active_animations["padding-right"]
    assert isinstance(right_anim, GenericPropertyAnimation)
    assert right_anim.current_val == pytest.approx(30.0)

    ctx.active_pseudos = set()
    engine.evaluate_widget_state(widget)
    for side, expected_start, expected_end in (
        ("padding-top", 18.0, 10.0),
        ("padding-right", 30.0, 16.0),
        ("padding-bottom", 18.0, 10.0),
        ("padding-left", 30.0, 16.0),
    ):
        back = ctx.active_animations[side]
        assert isinstance(back, GenericPropertyAnimation)
        assert back.anim.duration() == 900, side
        assert back.anim.easingCurve().type() == QEasingCurve.Type.OutCubic, side
        assert back.current_val == pytest.approx(expected_start), side
        assert float(back.anim.endValue()) == pytest.approx(expected_end), side
        assert back.anim.state() == QAbstractAnimation.State.Running, side
    destroy(widget)
