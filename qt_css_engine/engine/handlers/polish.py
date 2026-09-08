"""Polish queue — deferred evaluation after a Polish burst."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtWidgets import QWidget

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine

__all__ = ["PolishQueue"]


class PolishQueue:
    def __init__(self) -> None:
        self.pending: bool = False
        self.queue: list[QWidget] = []
        self.force_ids: set[int] = set()

    def enqueue(self, widget: QWidget, *, force: bool = False, schedule_flush: Callable[[], None]) -> None:
        if force:
            self.force_ids.add(id(widget))
        if not self.pending:
            self.pending = True
            self.queue.clear()
            schedule_flush()
        self.queue.append(widget)

    def flush(self, engine: TransitionEngine) -> None:
        self.pending = False
        widgets, self.queue = self.queue, []
        force_ids, self.force_ids = self.force_ids, set()
        seen: set[int] = set()
        for w in widgets:
            try:
                wid = id(w)
                if wid in seen:
                    continue
                seen.add(wid)
                engine.ensure_wa_hover(w)
                engine.seed_active_pseudo(w)
                ctx = engine.store.get(w)
                if wid in force_ids or ctx is None or not ctx.active_animations:
                    engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
