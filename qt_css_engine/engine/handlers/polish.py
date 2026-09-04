"""Polish queue — deferred evaluation after a Polish burst.

Owns pending/queue/force_ids. Qt scheduling and widget behavior stay in
TransitionEngine, passed in as callbacks, so this module needs no engine import.
"""

from collections.abc import Callable

from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.types import WidgetContext

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

    def flush(
        self,
        *,
        ensure_wa_hover: Callable[[QWidget], None],
        seed_active_pseudo: Callable[[QWidget], None],
        get_context: Callable[[QWidget], WidgetContext | None],
        evaluate: Callable[[QWidget, EvaluationCause], None],
    ) -> None:
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
                ensure_wa_hover(w)
                seed_active_pseudo(w)
                ctx = get_context(w)
                if wid in force_ids or ctx is None or not ctx.active_animations:
                    evaluate(w, EvaluationCause.POLISH)
            except RuntimeError:
                pass
