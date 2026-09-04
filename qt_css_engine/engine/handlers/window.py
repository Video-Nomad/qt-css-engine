"""Window activate/deactivate — :active and stuck pseudo cleanup."""

import logging
from typing import TYPE_CHECKING

from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtCore import QObject
from qt_css_engine.qt_compat.QtWidgets import QWidget

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine

event_logger = logging.getLogger("qt_css_engine.event")


def handle_window_activate(engine: TransitionEngine, widget: QWidget) -> None:
    for child in engine.active_rule_widgets.values():
        try:
            if child.window() is not widget:
                continue
        except RuntimeError:
            continue
        ctx = engine.get_context(child)
        if ":active" not in ctx.active_pseudos:
            event_logger.debug("On window activate: %s", widget)
            ctx.active_pseudos.add(":active")
            engine.evaluate_widget_state(child, cause=EvaluationCause.PSEUDO_STATE)


def handle_window_deactivate(engine: TransitionEngine, widget: QWidget, *, clear_active: bool = True) -> None:
    transients = {":hover", ":pressed", ":active"} if clear_active else {":hover", ":pressed"}
    store = engine.store
    stuck_ids = [wid for wid, ctx in store.contexts.items() if not ctx.active_pseudos.isdisjoint(transients)]
    for wid in stuck_ids:
        ctx = store.contexts.get(wid)
        child = store.widgets.get(wid)
        if ctx is None or child is None:
            continue
        stuck = ctx.active_pseudos & transients
        try:
            parent: QObject | None = child.parent()
            is_desc = False
            while parent is not None:
                if parent is widget:
                    is_desc = True
                    break
                parent = parent.parent()
            if not is_desc:
                continue
        except RuntimeError:
            continue
        event_logger.debug("Clearing stuck pseudos: %s", child)
        ctx.active_pseudos -= stuck
        engine.evaluate_widget_state(child, cause=EvaluationCause.WINDOW_DEACTIVATE)
