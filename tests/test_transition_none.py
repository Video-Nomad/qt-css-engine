# pyright: reportUnknownMemberType=false

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget


@pytest.mark.parametrize("reset", ["transition: none;", "transition-property: none;"])
@pytest.mark.parametrize("suffix", ["", "transition-duration: 600ms; transition-delay: 50ms;"])
def test_none_clears_accumulated_block(reset: str, suffix: str) -> None:
    cleaned, rules = extract_rules(".box { transition: all 300ms; transition: color 100ms; " + reset + suffix + " }")
    assert rules[0].resets_transitions
    assert rules[0].transitions == []
    assert "transition" not in cleaned


@pytest.mark.parametrize("reset", ["transition: NONE;", "transition-property: NoNe;", "transition: none 500ms;"])
def test_none_allows_subsequent_transition(reset: str) -> None:
    _, rules = extract_rules(".box { transition: color 100ms; " + reset + " transition: all 500ms; }")
    assert rules[0].resets_transitions
    assert [(t.prop, t.duration_ms) for t in rules[0].transitions] == [("all", 500)]


def test_property_none_preserves_timing_for_explicit_reenable() -> None:
    _, rules = extract_rules("""
        .box {
            transition: all 300ms linear 50ms;
            transition-property: none;
            transition-duration: 600ms;
            transition-property: color;
        }
    """)
    assert rules[0].resets_transitions
    assert [(t.prop, t.duration_ms, t.easing, t.delay_ms) for t in rules[0].transitions] == [
        ("color", 600, "linear", 50)
    ]


def test_subcontrol_none_does_not_reset_widget() -> None:
    _, rules = extract_rules(".box::item { transition: none; }")
    assert not rules[0].resets_transitions


@pytest.mark.parametrize(
    ("declarations", "expected"),
    [
        (".box { transition: all 300ms; } .box { transition: none; }", {}),
        (".box { transition: none; } .box { transition: all 300ms; }", {"color": 300, "opacity": 300}),
        ("#special { transition: none; } .box { transition: all 300ms; }", {}),
        ("#special { transition: color 100ms; } .box { transition: none; }", {"color": 100}),
        (
            ".box { transition: color 100ms; } #special { transition: none; transition: all 500ms; }",
            {"color": 500, "opacity": 500},
        ),
        (".box { transition: all 300ms; } #special { transition: none; transition: color 150ms; }", {"color": 150}),
        (".box { transition: all 300ms; transition: color 100ms; transition: none; }", {}),
        (".box { transition: none; transition: all 300ms; transition: color 100ms; }", {"color": 100, "opacity": 300}),
        (
            ".box { transition: none; transition: all 300ms; transition: none; transition: color 100ms; }",
            {"color": 100},
        ),
        (".box { transition: all 300ms; } .box:pressed { transition: none; }", {"color": 300, "opacity": 300}),
        (".box { transition: all 300ms; } .box[quiet=true] { transition: none; }", {"color": 300, "opacity": 300}),
        (".box:pressed { transition: none; } .box:hover { transition: all 300ms; }", {}),
    ],
)
def test_none_cascade_precedence(_app: QApplication, qtbot: QtBot, declarations: str, expected: dict[str, int]) -> None:
    _, rules = extract_rules(".box { color: red; opacity: 1; } " + declarations)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    widget.setObjectName("special")
    ctx = engine.get_context(widget)
    # The final case checks pressed priority even though hover occurs later.
    if declarations.startswith(".box:pressed"):
        ctx.active_pseudos = {":hover", ":pressed"}
    state = engine.cascade.collect(widget, ctx)
    assert {p: t.duration_ms for p, t in state.transitions.items()} == expected


@pytest.mark.parametrize("reset", ["transition: none;", "transition-property: none;"])
def test_none_snaps_hover_entry_and_animates_exit(_app: QApplication, qtbot: QtBot, reset: str) -> None:
    cleaned, rules = extract_rules(
        ".box { color: red; transition: all 300ms; } .box:hover { color: blue; " + reset + " }"
    )
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    widget.setStyleSheet(cleaned)
    assert "color: blue" not in cleaned  # The engine must still write the stripped target.
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    assert QColor(ctx.css_anim_props["color"]) == QColor("blue")
    assert not ctx.active_animations
    ctx.active_pseudos.clear()
    engine.evaluate_widget_state(widget)
    animation = ctx.active_animations["color"]
    assert isinstance(animation, ColorAnimation)
    assert animation.anim.state() == QAbstractAnimation.State.Running
    animation.anim.setCurrentTime(300)
    assert animation.current_color == QColor("red")


@pytest.mark.parametrize("class_driven", [False, True])
def test_none_stops_running_animation_at_new_target(_app: QApplication, qtbot: QtBot, class_driven: bool) -> None:
    _, rules = extract_rules("""
        .box { color: red; transition: color 1000ms linear; }
        .box:hover { color: blue; }
        .box:hover:pressed { color: green; transition: none; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget, cause=EvaluationCause.CLASS_CHANGE if class_driven else EvaluationCause.DIRECT)
    animation = ctx.active_animations["color"]
    assert isinstance(animation, ColorAnimation)
    animation.anim.setCurrentTime(200)
    assert animation.anim.state() == QAbstractAnimation.State.Running
    ctx.active_pseudos.add(":pressed")
    engine.evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)
    assert animation.anim.state() == QAbstractAnimation.State.Stopped
    assert animation.current_color == QColor("green")
    assert "color" not in ctx.class_anim_props
    assert "color" not in ctx.class_anim_callbacks


def test_none_cancels_delay_and_restores_timing_when_attribute_clears(_app: QApplication, qtbot: QtBot) -> None:
    _, rules = extract_rules("""
        .box { color: red; transition: all 300ms ease 500ms; }
        .box:hover { color: blue; }
        .box[quiet=true] { transition: none; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    assert ctx.pending_delays["color"].isActive()
    widget.setProperty("quiet", True)
    engine.evaluate_widget_state(widget)
    assert not ctx.pending_delays
    assert QColor(ctx.css_anim_props["color"]) == QColor("blue")
    assert not ctx.active_animations
    widget.setProperty("quiet", False)
    ctx.active_pseudos.clear()
    engine.evaluate_widget_state(widget)
    assert ctx.pending_delays["color"].isActive()


def test_none_after_class_removal_settles_managed_value(_app: QApplication, qtbot: QtBot) -> None:
    _, rules = extract_rules("""
        .animated { color: red; transition: all 300ms ease 500ms; }
        .animated:hover { color: blue; }
        .quiet { color: green; transition: none; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "animated")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    assert ctx.pending_delays
    widget.setProperty("class", "quiet")
    engine.on_class_change(widget)
    assert not ctx.pending_delays
    assert QColor(ctx.css_anim_props["color"]) == QColor("green")


def test_none_releases_running_clicked_cycle(_app: QApplication, qtbot: QtBot) -> None:
    _, rules = extract_rules("""
        .box { color: red; transition: color 1000ms; }
        .box:clicked { color: blue; }
        .box[quiet=true] { transition: none; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    updated: set[str] = set()
    cause = engine.prepare_clicked(widget, ctx, updated)
    ctx.active_pseudos.update(updated)
    engine.evaluate_widget_state(widget, cause=cause)
    engine.finish_clicked_activation(widget, ctx)
    assert ctx.clicked_anim_props == {"color"}
    assert ctx.active_animations["color"].anim.state() == QAbstractAnimation.State.Running
    widget.setProperty("quiet", True)
    engine.evaluate_widget_state(widget)
    assert not ctx.clicked_anim_props
    QApplication.processEvents()
    assert ":clicked" not in ctx.active_pseudos
    assert QColor(ctx.css_anim_props["color"]) == QColor("red")


def test_none_snaps_effect_and_cleans_effect_when_rule_disappears(_app: QApplication, qtbot: QtBot) -> None:
    _, rules = extract_rules("""
        .box { opacity: 1; transition: all 1000ms; }
        .box:hover { opacity: 0.5; }
        .box:pressed { opacity: 0.25; transition: none; }
        .quiet { transition: none; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    animation = ctx.active_animations["opacity"]
    assert animation.anim.state() == QAbstractAnimation.State.Running
    ctx.active_pseudos.add(":pressed")
    engine.evaluate_widget_state(widget)
    assert animation.anim.state() == QAbstractAnimation.State.Stopped
    effect = widget.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    assert effect.opacity() == pytest.approx(0.25)
    widget.setProperty("class", "quiet")
    engine.on_class_change(widget)
    assert "opacity" not in ctx.active_animations
    assert widget.graphicsEffect() is None
