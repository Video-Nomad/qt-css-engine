# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""Regression tests for bugs found during edge-case audit."""

import warnings

import pytest

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.factory import create_animator
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QEasingCurve
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget
from qt_css_engine.utils.qt_helpers import safe_disconnect


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


def test_factory_opacity_invalid_initial_falls_back_to_zero(_app: QApplication) -> None:
    """Invalid opacity value must not raise ValueError (was: float('foo') crash)."""
    widget = QWidget()
    try:
        anim = create_animator(widget, "opacity", "foo", 200, QEasingCurve.Type.Linear)
        assert isinstance(anim, OpacityAnimation)
        assert anim._current_val == pytest.approx(0.0)
        anim.anim.stop()
    finally:
        destroy(widget)


def test_factory_opacity_empty_initial_falls_back_to_zero(_app: QApplication) -> None:
    widget = QWidget()
    try:
        anim = create_animator(widget, "opacity", "", 200, QEasingCurve.Type.Linear)
        assert isinstance(anim, OpacityAnimation)
        assert anim._current_val == pytest.approx(0.0)
        anim.anim.stop()
    finally:
        destroy(widget)


def test_zero_duration_radius_snap_normalized_format(_app: QApplication) -> None:
    """Snap path must write the same 3-decimal format as animation ticks."""
    engine = make_engine("""
        .box { border-radius: 0px; }
        .box:hover { border-radius: 12px; transition: border-radius 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(100, 100)
    try:
        engine.get_context(widget).active_pseudos = {":hover"}
        engine.evaluate_widget_state(widget)
        ctx = engine.get_context(widget)
        assert ctx.css_anim_props.get("border-top-left-radius") == "12.000px"
    finally:
        destroy(widget)


def test_window_activate_skips_child_of_other_window(_app: QApplication) -> None:
    """Child whose window() is not the activated widget must be skipped."""
    engine = make_engine(".t:active { background-color: blue; transition: background-color 200ms; }")
    window_a = QWidget()
    window_b = QWidget()
    child_b = QWidget(window_b)
    child_b.setProperty("class", "t")
    engine.seed_active_pseudo(child_b)
    try:
        engine.on_window_activate(window_a)
        assert ":active" not in engine.get_context(child_b).active_pseudos
    finally:
        destroy(child_b)
        destroy(window_a)
        destroy(window_b)


def test_seed_active_pseudo_adds_active_when_window_active(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    """Active window + matching :active rule with transition → :active added."""
    engine = make_engine(".t:active { background-color: blue; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "t")
    try:
        monkeypatch.setattr(widget, "isActiveWindow", lambda: True)
        assert engine.should_evaluate(widget) is True
        engine.seed_active_pseudo(widget)
        assert ":active" in engine.get_context(widget).active_pseudos
    finally:
        destroy(widget)


def test_safe_disconnect_double_disconnect_emits_no_warning(
    _app: QApplication, recwarn: pytest.WarningsRecorder
) -> None:
    """Second disconnect of the same callback must be silent on PySide."""
    widget = QWidget()
    try:
        from qt_css_engine.animation.numeric import GenericPropertyAnimation
        from qt_css_engine.types import WidgetContext

        anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=WidgetContext())
        cb = lambda: None
        anim.anim.finished.connect(cb)
        safe_disconnect(anim.anim.finished, cb)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            safe_disconnect(anim.anim.finished, cb)
        anim.anim.stop()
        runtime_warnings = [w for w in recwarn.list if issubclass(w.category, RuntimeWarning)]
        assert runtime_warnings == []
    finally:
        destroy(widget)
