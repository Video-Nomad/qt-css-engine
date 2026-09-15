"""Radius transitions must follow layout geometry without restarting their clock."""

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.qt_compat.QtCore import QEvent
from qt_css_engine.qt_compat.QtWidgets import QApplication, QFrame, QHBoxLayout, QLayout, QPushButton, QWidget


@pytest.mark.parametrize("height", [20, 80])
def test_resize_updates_radius_endpoint_without_restarting(_app: QApplication, qtbot: QtBot, height: int) -> None:
    _, rules = extract_rules("""
        .pill { border-radius: 12px; transition: border-radius 1000ms linear; }
        .pill:hover { border-radius: 99px; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "pill")
    widget.resize(200, 40)
    ctx = engine.get_context(widget)
    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)
    radius = ctx.active_animations["border-top-left-radius"]
    assert isinstance(radius, GenericPropertyAnimation)
    radius.anim.setCurrentTime(500)

    widget.resize(200, height)
    engine.eventFilter(widget, QEvent(QEvent.Type.Resize))

    assert radius.anim.currentTime() == 500
    assert radius.anim.state() == radius.anim.State.Running
    assert radius.anim.endValue() == height / 2
    radius.anim.setCurrentTime(1000)
    assert float(ctx.css_anim_props["border-top-left-radius"].removesuffix("px")) == height / 2


@pytest.mark.parametrize("radius_ms", [300, 1000])
@pytest.mark.parametrize("easing", ["ease", "cubic-bezier(0.175, 1.2, 0.32, 1.2)"])
def test_nested_pill_follows_padding_layout(_app: QApplication, qtbot: QtBot, radius_ms: int, easing: str) -> None:
    css, rules = extract_rules(
        """
        .nest-row .nest-pill {
            background-color: #20202a;
            border: 2px solid #3a3a46;
            border-radius: 12px;
            padding: 4px;
            transition: all 1000ms ease;
        }
        .nest-row .nest-pill:hover {
            padding: 8px;
            border-radius: 99px;
            border-color: #4d88ff;
            min-width: 210px;
            max-width: 210px;
        }
        .nest-mini {
            border: 2px solid #3a3a46;
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 12px;
        }
    """.replace(
            "transition: all 1000ms ease;", f"transition: all 1000ms {easing}, border-radius {radius_ms}ms {easing};"
        )
    )
    engine = TransitionEngine(rules, startup_delay_ms=0)
    root = QWidget()
    qtbot.addWidget(root)
    root.setProperty("class", "nest-row")
    root.setStyleSheet(css)
    layout = QHBoxLayout(root)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
    pill = QFrame()
    pill.setProperty("class", "nest-pill")
    inner = QHBoxLayout(pill)
    inner.setContentsMargins(6, 6, 6, 6)
    for i in range(2):
        button = QPushButton(f"0-{i}")
        button.setProperty("class", "nest-mini")
        inner.addWidget(button)
    layout.addWidget(pill)
    root.ensurePolished()
    root.adjustSize()
    layout.activate()
    initial_height = pill.height()
    ctx = engine.get_context(pill)

    def advance_transition() -> None:
        for time_ms in (100, 300, 500, 900, 1000):
            for animation in ctx.active_animations.values():
                if animation.anim.state() == animation.anim.State.Running:
                    animation.anim.setCurrentTime(min(time_ms, animation.anim.duration()))
            engine.writer.flush_now(pill, ctx)
            root.adjustSize()
            layout.activate()
            # Hidden widgets defer Resize delivery until show(); deliver it explicitly
            # after assigning the real layout geometry, without entering the event loop.
            engine.eventFilter(pill, QEvent(QEvent.Type.Resize))
            assert all(
                float(value.removesuffix("px")) <= min(pill.width(), pill.height()) // 2
                for prop, value in ctx.css_anim_props.items()
                if prop.endswith("-radius")
            )

    ctx.active_pseudos = {":hover"}
    engine.evaluate_widget_state(pill)
    advance_transition()

    assert pill.height() > initial_height
    assert {
        float(value.removesuffix("px")) for prop, value in ctx.css_anim_props.items() if prop.endswith("-radius")
    } == {min(pill.width(), pill.height()) // 2}
    ctx.active_pseudos.clear()
    engine.evaluate_widget_state(pill)
    advance_transition()
    assert pill.height() == initial_height
    assert {
        float(value.removesuffix("px")) for prop, value in ctx.css_anim_props.items() if prop.endswith("-radius")
    } == {12.0}
