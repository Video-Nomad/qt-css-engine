"""Parent change handler — invalidate and re-queue subtree."""

from typing import TYPE_CHECKING

from qt_css_engine.qt_compat.QtWidgets import QWidget

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine


def handle_parent_change(engine: TransitionEngine, widget: QWidget) -> None:
    engine.matcher.invalidate_widget(widget)
    if not engine.matcher.index.flags.has_descendant:
        if engine.should_evaluate(widget):
            engine.queue_polish_evaluation(widget, force=True)
        return
    subtree = (widget, *widget.findChildren(QWidget))
    for w in subtree:
        engine.matcher.invalidate_widget(w)
    for w in subtree:
        try:
            if engine.should_evaluate(w):
                engine.queue_polish_evaluation(w, force=True)
        except RuntimeError:
            pass
