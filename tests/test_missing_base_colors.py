"""Color transitions must work before an omitted base value has been written inline."""

import pytest

from qt_css_engine import TransitionEngine, extract_rules
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEvent
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget
from qt_css_engine.utils.color import parse_color


@pytest.mark.parametrize("polished", [False, True])
@pytest.mark.parametrize("transition_all", [False, True], ids=["explicit", "all"])
@pytest.mark.parametrize("css_prop", ["background", "background-color", "color"])
def test_missing_base_color_animates_on_first_hover(
    _app: QApplication, css_prop: str, transition_all: bool, polished: bool
) -> None:
    transition_prop = "all" if transition_all else css_prop
    _, rules = extract_rules(f"""
        .item {{ transition: {transition_prop} 1000ms linear; }}
        .item:hover {{ {css_prop}: red; }}
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    widget = QWidget()
    widget.setProperty("class", "item")
    prop = "background-color" if css_prop == "background" else css_prop
    base_color = QColor("white" if prop == "color" else "transparent")
    target_color = QColor("red")
    try:
        if polished:
            engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)

        engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))

        ctx = engine.get_context(widget)
        animation = ctx.active_animations.get(prop)
        assert isinstance(animation, ColorAnimation)
        assert animation.anim.state() == QAbstractAnimation.State.Running
        assert animation.start_color == base_color
        assert animation.end_color == target_color
        assert parse_color(ctx.css_anim_props[prop]) == base_color

        animation.anim.setCurrentTime(500)
        assert animation.current_color != base_color
        assert animation.current_color != target_color
        engine.writer.flush_now(widget, ctx)
        assert ctx.css_anim_props[prop] in widget.styleSheet()

        animation.anim.setCurrentTime(1000)
        assert parse_color(ctx.css_anim_props[prop]) == target_color

        engine.eventFilter(widget, QEvent(QEvent.Type.HoverLeave))
        assert animation.anim.state() == QAbstractAnimation.State.Running
        assert animation.start_color == target_color
        assert animation.end_color == base_color
        animation.anim.setCurrentTime(1000)
        assert parse_color(ctx.css_anim_props[prop]) == base_color

        engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))
        assert animation.anim.state() == QAbstractAnimation.State.Running
        assert animation.start_color == base_color
        assert animation.end_color == target_color
    finally:
        qt_delete(widget)
        qt_delete(engine)
