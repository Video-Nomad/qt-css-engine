"""Delay scheduler — QTimer per prop for transition-delay."""

from collections.abc import Callable

from qt_css_engine.qt_compat.QtCore import QObject, QTimer
from qt_css_engine.types import WidgetContext
from qt_css_engine.utils.qt_helpers import safe_disconnect


class DelayScheduler(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def schedule(self, ctx: WidgetContext, prop: str, delay_ms: int, callback: Callable[[], None]) -> None:
        self.cancel(ctx, prop)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        ctx.pending_delays[prop] = timer
        timer.start(delay_ms)

    def cancel(self, ctx: WidgetContext, prop: str) -> None:
        timer = ctx.pending_delays.pop(prop, None)
        if timer is not None:
            try:
                timer.stop()
                safe_disconnect(timer.timeout)
                timer.deleteLater()
            except RuntimeError:
                pass

    def cancel_all(self, ctx: WidgetContext) -> None:
        for prop in list(ctx.pending_delays):
            self.cancel(ctx, prop)
