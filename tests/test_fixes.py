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
from qt_css_engine.qt_compat.QtCore import QEasingCurve, Qt
from qt_css_engine.qt_compat.QtWidgets import QApplication, QFrame, QLabel, QWidget
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
        from qt_css_engine.state.widget_state import WidgetState

        anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=WidgetState())
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


def test_numeric_finish_explicit_flushes_immediately(_app: QApplication) -> None:
    """Explicit-target finish must paint same turn, not lag one batched frame.

    Regression for cycle .active ghost: last tick wrote 140px to the dict but
    the stylesheet still showed the penultimate overshoot (145px) until the
    singleShot(0) flush fired — one frame of stale layout. _on_finished must
    force immediate flush so dict and stylesheet agree synchronously.
    """
    from qt_css_engine.animation.numeric import GenericPropertyAnimation

    engine = make_engine(".x { transition: min-width 300ms; }")
    widget = QWidget()
    widget.setProperty("class", "x")
    ctx = engine.get_context(widget)
    try:
        anim = GenericPropertyAnimation(
            widget,
            "min-width",
            140.0,
            300,
            QEasingCurve.Type.Linear,
            parent=engine,
            ctx=ctx,
            style_flush_callback=lambda w, c: engine.writer.schedule(w, c),
        )
        anim._clean_on_finish = False
        # Simulate batched lag: dict already holds final, stylesheet still penultimate.
        ctx.css_anim_props["min-width"] = "140.000px"
        ctx.style_flush_pending = True
        ctx.applied_style = 'QWidget[_anim_scope="stale"] { min-width: 145.000px; }'
        widget.setProperty("_anim_scope", "stale")
        widget.setStyleSheet('QWidget[_anim_scope="stale"] { min-width: 145.000px; }')
        anim.anim.stop()

        anim._on_finished()

        assert ctx.style_flush_pending is False
        assert "140.000px" in widget.styleSheet()
        assert "145.000px" not in widget.styleSheet()
        anim.anim.stop()
    finally:
        destroy(widget)


def test_numeric_finish_clean_removes_inline_immediately(_app: QApplication) -> None:
    """Natural-target (clean) finish must delete + paint synchronously.

    Same ghost as explicit: batched delete left the old inline constraint in
    the stylesheet for one extra frame while the dict already dropped it.
    """
    from qt_css_engine.animation.numeric import GenericPropertyAnimation

    engine = make_engine(".x { transition: min-width 300ms; }")
    widget = QWidget()
    widget.setProperty("class", "x")
    ctx = engine.get_context(widget)
    try:
        anim = GenericPropertyAnimation(
            widget,
            "min-width",
            27.0,
            300,
            QEasingCurve.Type.Linear,
            parent=engine,
            ctx=ctx,
            style_flush_callback=lambda w, c: engine.writer.schedule(w, c),
        )
        anim._clean_on_finish = True
        ctx.css_anim_props["min-width"] = "27.000px"
        ctx.style_flush_pending = True
        widget.setProperty("_anim_scope", "stale2")
        widget.setStyleSheet('QWidget[_anim_scope="stale2"] { min-width: 27.000px; }')
        ctx.applied_style = widget.styleSheet()
        anim.anim.stop()

        anim._on_finished()

        assert "min-width" not in ctx.css_anim_props
        assert ctx.style_flush_pending is False
        assert "min-width" not in widget.styleSheet()
        anim.anim.stop()
    finally:
        destroy(widget)


def test_color_finish_flushes_immediately(_app: QApplication) -> None:
    """Color finish must not leave the end color pending one batched frame."""
    from qt_css_engine.animation.color import ColorAnimation

    engine = make_engine(".x { transition: background-color 300ms; }")
    widget = QWidget()
    widget.setProperty("class", "x")
    ctx = engine.get_context(widget)
    try:
        anim = ColorAnimation(
            widget,
            "background-color",
            "#2e2e38",
            300,
            QEasingCurve.Type.Linear,
            parent=engine,
            ctx=ctx,
            style_flush_callback=lambda w, c: engine.writer.schedule(w, c),
        )
        anim.set_target("#4d88ff")
        anim.anim.stop()
        # Simulate lag: dict holds end color, stylesheet still shows start.
        end_name = anim.end_color.name()
        ctx.css_anim_props["background-color"] = end_name
        ctx.style_flush_pending = True
        widget.setProperty("_anim_scope", "cstale")
        widget.setStyleSheet('QWidget[_anim_scope="cstale"] { background-color: #2e2e38; }')
        ctx.applied_style = widget.styleSheet()

        anim._on_finished()

        assert ctx.style_flush_pending is False
        assert end_name in widget.styleSheet()
        anim.anim.stop()
    finally:
        destroy(widget)


def test_post_clean_noop_skips_natural_measurement(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-clean CLASS_ANIMATION_FINISH must not measure natural size.

    After clean_on_finish drops the inline constraint, resolving would call
    get_natural_size (strip/restore + parent-layout activates with
    updatesEnabled toggling) only for _is_natural_noop to discard it. That
    shared-parent thrash at finish time paints as a one-frame ghost.
    """
    from qt_css_engine.engine.evaluation import EvaluationCause, ResolvedRuleState
    from qt_css_engine.engine.evaluator import Evaluation

    engine = make_engine(".x { transition: min-width 300ms; }")
    widget = QWidget()
    widget.setProperty("class", "x")
    ctx = engine.get_context(widget)
    try:
        from qt_css_engine.animation.numeric import GenericPropertyAnimation

        anim = GenericPropertyAnimation(
            widget, "min-width", 33.0, 200, QEasingCurve.Type.Linear, parent=engine, ctx=ctx
        )
        ctx.active_animations["min-width"] = anim
        ctx.css_anim_props.clear()  # post-clean: inline already removed
        anim.anim.stop()

        calls: list[str] = []
        orig_natural = engine.evaluator.get_natural_size

        def spy(widget_: QWidget, base_props_: dict[str, str], prop_: str, current_raw_: str | None = None) -> str:
            calls.append(prop_)
            return orig_natural(widget_, base_props_, prop_, current_raw_)

        monkeypatch.setattr(engine.evaluator, "get_natural_size", spy)
        needs_update = engine.evaluator.apply_prop(
            Evaluation(widget, ctx, ResolvedRuleState(), EvaluationCause.CLASS_ANIMATION_FINISH),
            "min-width",
        )
        assert needs_update is False
        assert calls == [], f"post-clean noop must not measure natural size, got {calls}"
        anim.anim.stop()
    finally:
        destroy(widget)


def test_hidden_ancestor_class_change_deferred_to_polish(_app: QApplication) -> None:
    """Hidden ancestor class sets must not write scoped inline styles immediately.

    Regression (YASB adaptive startup): writing the ancestor inline before
    descendants exist/are polished makes Qt drop ancestor-dependent descendant
    QSS (e.g. `.active-window-widget .icon`) on first polish. The class change
    must defer to the Polish burst on show; visible widgets keep the sync path.
    """
    engine = make_engine("""
        .outer { background-color: red; transition: background-color 100ms; }
        .outer .inner { padding-right: 20px; }
    """)
    _app.installEventFilter(engine)
    parent = QFrame()
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    try:
        assert not parent.isVisible()
        parent.setProperty("class", "outer")
        child = QLabel("x", parent)
        child.setProperty("class", "inner")
        _app.processEvents()
        # Deferred: no context/inline created while the tree is hidden.
        assert id(parent) not in engine.store.contexts
        assert parent.styleSheet() == ""
        parent.show()
        _app.processEvents()
        _app.processEvents()
        ctx = engine.store.contexts.get(id(parent))
        assert ctx is not None
        assert ctx.css_anim_props.get("background-color") is not None
    finally:
        destroy(parent)
