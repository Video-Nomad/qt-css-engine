"""Clicked lifecycle — :clicked forward/reverse activation."""

from typing import TYPE_CHECKING

from qt_css_engine.animation.callbacks import next_clicked_gen
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QTimer
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine


def prepare_clicked(engine: TransitionEngine, widget: QWidget, ctx: WidgetState, updated: set[str]) -> EvaluationCause:
    """Add :clicked tracking when matching rules exist; return the cause to evaluate with."""
    if ":clicked" in ctx.active_pseudos:
        return EvaluationCause.PSEUDO_STATE
    clicked_rules = [rule for rule in engine.matcher.matching_rules(widget) if ":clicked" in rule.pseudo_set]
    if not clicked_rules:
        return EvaluationCause.PSEUDO_STATE
    updated.add(":clicked")
    clicked_props: set[str] = set()
    for rule in clicked_rules:
        clicked_props.update(rule.properties.keys())
    next_clicked_gen(ctx, clicked_props)
    return EvaluationCause.CLICKED_ACTIVATION


def finish_clicked_activation(engine: TransitionEngine, widget: QWidget, ctx: WidgetState) -> None:
    """Prune snapped clicked props; deactivate immediately when nothing is running."""
    ctx.clicked_anim_props = {
        p
        for p in ctx.clicked_anim_props
        if p in ctx.active_animations and ctx.active_animations[p].anim.state() == QAbstractAnimation.State.Running
    }
    if not ctx.clicked_anim_props:
        wid = id(widget)
        gen = ctx.clicked_anim_gen
        QTimer.singleShot(0, lambda: deactivate_clicked(engine, widget, wid, gen))


def deactivate_clicked(engine: TransitionEngine, widget: QWidget, wid: int, gen: int) -> None:
    """Remove :clicked and re-evaluate to trigger the reverse animation."""
    ctx = engine.store.contexts.get(wid)
    if ctx is None or gen != ctx.clicked_anim_gen or ":clicked" not in ctx.active_pseudos:
        return
    ctx.active_pseudos.discard(":clicked")
    try:
        engine.evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)
    except RuntimeError:
        pass
