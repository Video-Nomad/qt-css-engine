"""Class change handler — snapshot + polish guard + re-evaluate."""

import logging
from typing import TYPE_CHECKING

from qt_css_engine.animation.callbacks import next_class_gen
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.suppress import suppress
from qt_css_engine.style.effects import update_shadow_ancestor
from qt_css_engine.types import InternalWriteReason

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine

event_logger = logging.getLogger("qt_css_engine.event")


def handle_class_change(engine: TransitionEngine, widget: QWidget) -> None:
    engine.matcher.invalidate_subtree(widget)
    if not engine.should_evaluate(widget):
        return
    # Early ancestor inlines make Qt drop descendant QSS on first polish, so
    # pristine hidden ancestors defer to Polish on show (details in test_fixes).
    try:
        visible = widget.isVisible()
    except RuntimeError:
        return
    if not visible:
        existing = engine.store.contexts.get(id(widget))
        if existing is None or (not existing.active_animations and not existing.css_anim_props):
            # Only potential ancestors defer; leaves evaluate immediately.
            try:
                ident = engine.matcher.identity(widget)
            except RuntimeError:
                return
            if engine.matcher.is_ancestor_relevant(ident):
                return
    event_logger.debug("On class change: %s", widget)
    ctx = engine.get_context(widget)
    ctx.pre_polish_size = (widget.width(), widget.height())
    with suppress(ctx, InternalWriteReason.CLASS_CHANGE):
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
    widget.update()
    update_shadow_ancestor(widget)
    next_class_gen(ctx)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.CLASS_CHANGE)
    ctx.pre_polish_size = None
