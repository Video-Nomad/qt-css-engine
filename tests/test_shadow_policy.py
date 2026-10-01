from collections.abc import Callable, Generator

import pytest

from qt_css_engine import TransitionEngine, extract_rules
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QCoreApplication, QEasingCurve
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import QApplication, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget
from qt_css_engine.style.effects import apply_opacity_to_widget, apply_shadow_to_widget
from qt_css_engine.utils.color import ShadowParams


@pytest.fixture
def widget(_app: QApplication) -> Generator[QWidget]:
    root = QWidget()
    yield root
    qt_delete(root)


@pytest.fixture
def engine_factory(_app: QApplication) -> Generator[Callable[[str], TransitionEngine]]:
    engines: list[TransitionEngine] = []

    def make(css: str) -> TransitionEngine:
        _, rules = extract_rules(css)
        engine = TransitionEngine(rules, startup_delay_ms=0)
        engines.append(engine)
        _app.installEventFilter(engine)
        return engine

    yield make
    for engine in engines:
        _app.removeEventFilter(engine)


@pytest.mark.parametrize("prop", ["box-shadow", "text-shadow"])
@pytest.mark.parametrize("transition", ["", "box-shadow 1000ms linear 50ms", "all 1000ms linear 50ms"])
def test_opt_out_blocks_static_and_hover_shadows(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine], prop: str, transition: str
) -> None:
    widget.setProperty("cssEngineDisableShadow", True)
    widget.setProperty("class", "bar")
    engine = engine_factory(f"""
        .bar {{ {prop}: 0 0 10 red; opacity: 0.5; transition: {transition or "none"}; }}
        .bar:hover {{ {prop}: 0 0 20 blue; }}
    """)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    ctx = engine.get_context(widget)
    assert "box-shadow" not in ctx.active_animations
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    assert "box-shadow" not in ctx.active_animations
    assert "box-shadow" not in ctx.pending_delays
    assert getattr(widget, "_desired_shadow", None) is None
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)


def test_opt_out_is_local_and_survives_reload(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine]
) -> None:
    widget.setProperty("class", "bar")
    widget.setProperty("cssEngineDisableShadow", True)
    child = QWidget(widget)
    child.setProperty("class", "bar")
    engine = engine_factory(".bar { box-shadow: 0 0 10 red; }")
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    engine.evaluate_widget_state(child, cause=EvaluationCause.POLISH)
    assert widget.graphicsEffect() is None
    assert isinstance(child.graphicsEffect(), QGraphicsDropShadowEffect)

    _, rules = extract_rules(".bar { text-shadow: 0 0 20 blue; transition: all 1000ms linear; }")
    engine.reload_rules(rules)
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()
    assert widget.property("cssEngineDisableShadow") is True
    assert widget.graphicsEffect() is None
    assert "box-shadow" not in engine.get_context(widget).active_animations
    effect = child.graphicsEffect()
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.color() == QColor("blue")
    assert effect.blurRadius() == 20


@pytest.mark.parametrize("enable_value", [False, None])
def test_runtime_opt_out_removes_shadow_without_retiming_other_animations(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine], enable_value: bool | None
) -> None:
    widget.setProperty("class", "bar")
    engine = engine_factory("""
        .bar {
            box-shadow: 0 0 10 red;
            background-color: black;
            transition: box-shadow 1000ms linear, background-color 1000ms linear;
        }
        .bar.active { box-shadow: 0 0 20 blue; background-color: white; }
    """)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    QCoreApplication.processEvents()
    widget.setProperty("class", "bar active")
    ctx = engine.get_context(widget)
    shadow = ctx.active_animations["box-shadow"]
    color = ctx.active_animations["background-color"]
    assert isinstance(shadow, BoxShadowHandle)
    assert isinstance(color, ColorAnimation)
    shadow.anim.setCurrentTime(300)
    color.anim.setCurrentTime(300)

    widget.setProperty("cssEngineDisableShadow", True)
    assert widget.graphicsEffect() is None
    assert getattr(widget, "_desired_shadow", "unset") is None
    assert "box-shadow" not in ctx.active_animations
    assert "box-shadow" not in ctx.class_anim_props
    assert "box-shadow" not in ctx.class_anim_callbacks
    assert shadow.anim.state() == QAbstractAnimation.State.Stopped
    assert ctx.active_animations["background-color"] is color
    assert color.anim.state() == QAbstractAnimation.State.Running
    assert color.anim.currentTime() == 300

    widget.setProperty("cssEngineDisableShadow", enable_value)
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.color() == QColor("blue")
    assert effect.blurRadius() == 20
    restored = ctx.active_animations["box-shadow"]
    assert isinstance(restored, BoxShadowHandle)
    assert restored.anim.state() == QAbstractAnimation.State.Stopped
    assert color.anim.state() == QAbstractAnimation.State.Running
    assert color.anim.currentTime() == 300


def test_runtime_opt_out_cancels_delayed_shadow(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine]
) -> None:
    widget.setProperty("class", "bar")
    engine = engine_factory("""
        .bar { box-shadow: none; transition: box-shadow 1000ms linear 60000ms; }
        .bar:hover { box-shadow: 0 0 10 red; }
    """)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    timer = ctx.pending_delays["box-shadow"]
    assert timer.isActive()

    widget.setProperty("cssEngineDisableShadow", True)
    assert "box-shadow" not in ctx.pending_delays
    assert "box-shadow" not in ctx.active_animations
    assert not timer.isActive()
    engine.evaluator.fire_delayed_prop(widget, "box-shadow")
    assert widget.graphicsEffect() is None
    assert "box-shadow" not in ctx.active_animations


def test_runtime_opt_out_releases_clicked_shadow(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine]
) -> None:
    widget.setProperty("class", "bar")
    engine = engine_factory("""
        .bar { box-shadow: none; transition: box-shadow 1000ms linear; }
        .bar:clicked { box-shadow: 0 0 10 red; }
    """)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    ctx = engine.get_context(widget)
    updated = set(ctx.active_pseudos)
    cause = engine.prepare_clicked(widget, ctx, updated)
    ctx.active_pseudos = updated
    engine.evaluate_widget_state(widget, cause=cause)
    engine.finish_clicked_activation(widget, ctx)
    assert "box-shadow" in ctx.clicked_anim_props
    assert "box-shadow" in ctx.clicked_anim_callbacks

    widget.setProperty("cssEngineDisableShadow", True)
    assert "box-shadow" not in ctx.clicked_anim_props
    assert "box-shadow" not in ctx.clicked_anim_callbacks
    QCoreApplication.processEvents()
    assert ":clicked" not in ctx.active_pseudos
    assert widget.graphicsEffect() is None


def test_runtime_opt_out_preserves_opacity_and_prevents_cached_shadow_restoration(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine]
) -> None:
    widget.setProperty("class", "bar")
    engine = engine_factory("""
        .bar { box-shadow: 0 0 10 red; opacity: 0.5; transition: opacity 1000ms linear; }
        .bar:hover { opacity: 1; }
    """)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    opacity = ctx.active_animations["opacity"]
    assert isinstance(opacity, OpacityAnimation)
    opacity.anim.setCurrentTime(300)
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert getattr(widget, "_desired_shadow", None) is not None

    widget.setProperty("cssEngineDisableShadow", True)
    assert widget.graphicsEffect() is effect
    assert getattr(widget, "_desired_shadow", "unset") is None
    assert opacity.anim.currentTime() == 300
    opacity.anim.setCurrentTime(1000)
    assert widget.graphicsEffect() is None


@pytest.mark.parametrize("priority", ["opacity", "box-shadow"])
def test_effect_helpers_honor_opt_out_without_engine_filter(widget: QWidget, priority: str) -> None:
    params = ShadowParams(0, 0, 10, 0, QColor("red"))
    apply_opacity_to_widget(widget, 0.5, "opacity")
    apply_shadow_to_widget(widget, params, "opacity")
    assert getattr(widget, "_desired_shadow", None) is params

    widget.setProperty("cssEngineDisableShadow", True)
    apply_opacity_to_widget(widget, 1.0, priority)
    assert widget.graphicsEffect() is None
    assert getattr(widget, "_desired_shadow", "unset") is None
    apply_shadow_to_widget(widget, params, priority)
    assert widget.graphicsEffect() is None
    apply_opacity_to_widget(widget, 0.5, priority)
    assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)


def test_runtime_opt_out_reveals_opacity_previously_blocked_by_shadow(
    widget: QWidget, engine_factory: Callable[[str], TransitionEngine]
) -> None:
    widget.setProperty("class", "bar")
    engine = engine_factory(".bar { box-shadow: 0 0 10 red; opacity: 0.5; }")
    engine.effect_priority = "box-shadow"
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect)

    widget.setProperty("cssEngineDisableShadow", True)
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert effect.opacity() == 0.5
    assert getattr(widget, "_desired_shadow", "unset") is None


def test_shadow_handle_cannot_install_shadow_while_disabled(widget: QWidget) -> None:
    widget.setProperty("cssEngineDisableShadow", True)
    animation = BoxShadowHandle(widget, "0 0 10 red", 1000, QEasingCurve.Type.Linear, parent=widget)
    assert widget.graphicsEffect() is None
    animation.set_target("0 0 20 blue")
    animation.anim.setCurrentTime(500)
    assert widget.graphicsEffect() is None
    animation.anim.setCurrentTime(1000)
    assert widget.graphicsEffect() is None
    assert getattr(widget, "_desired_shadow", "unset") is None
