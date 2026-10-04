"""Widget state store — owns contexts and observes widget lifetimes.

Pure storage only. Lifecycle wiring (destroyed signal, animation teardown)
lives in TransitionEngine, which owns this store.
"""

from weakref import WeakValueDictionary

from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState


class WidgetStore:
    """Maps id(widget) -> WidgetState plus a non-owning widget mirror."""

    def __init__(self) -> None:
        self.contexts: dict[int, WidgetState] = {}
        self.widgets: WeakValueDictionary[int, QWidget] = WeakValueDictionary()

    def get(self, widget: QWidget) -> WidgetState | None:
        return self.contexts.get(id(widget))

    def get_or_create(self, widget: QWidget) -> WidgetState:
        wid = id(widget)
        ctx = self.contexts.get(wid)
        if ctx is None:
            ctx = WidgetState()
            self.contexts[wid] = ctx
            self.widgets[wid] = widget
        return ctx

    def remove(self, wid: int) -> WidgetState | None:
        self.widgets.pop(wid, None)
        return self.contexts.pop(wid, None)

    def __contains__(self, widget: QWidget) -> bool:
        return id(widget) in self.contexts

    def clear(self) -> None:
        self.contexts.clear()
        self.widgets.clear()
