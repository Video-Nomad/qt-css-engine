"""Widget state store — owns contexts and widget mirrors.

Pure storage only. Lifecycle wiring (destroyed signal, animation teardown)
lives in TransitionEngine, which owns this store.
"""

from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.types import WidgetContext


class WidgetStore:
    """Maps id(widget) -> WidgetContext plus id -> widget mirror."""

    def __init__(self) -> None:
        self.contexts: dict[int, WidgetContext] = {}
        self.widgets: dict[int, QWidget] = {}

    def get(self, widget: QWidget) -> WidgetContext | None:
        return self.contexts.get(id(widget))

    def get_or_create(self, widget: QWidget) -> WidgetContext:
        wid = id(widget)
        ctx = self.contexts.get(wid)
        if ctx is None:
            ctx = WidgetContext()
            self.contexts[wid] = ctx
            self.widgets[wid] = widget
        return ctx

    def remove(self, wid: int) -> WidgetContext | None:
        self.widgets.pop(wid, None)
        return self.contexts.pop(wid, None)

    def __contains__(self, widget: QWidget) -> bool:
        return id(widget) in self.contexts

    def clear(self) -> None:
        self.contexts.clear()
        self.widgets.clear()
