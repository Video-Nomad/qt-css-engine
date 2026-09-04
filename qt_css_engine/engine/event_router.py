"""Event router — dispatch Qt events to handler methods."""

from typing import TYPE_CHECKING

from qt_css_engine.constants import ENGINE_EVENT_TYPES, PSEUDO_EVENTS
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat.QtCore import QEvent, QObject, Qt
from qt_css_engine.qt_compat.QtGui import QMouseEvent
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.pseudo import PseudoMachine

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine


class EventRouter:
    """Stateless helper for TransitionEngine.eventFilter routing."""

    @staticmethod
    def is_relevant(event_type: QEvent.Type, watched: QObject) -> bool:
        return event_type in ENGINE_EVENT_TYPES and isinstance(watched, QWidget)

    @staticmethod
    def is_pseudo(event_type: QEvent.Type) -> bool:
        return event_type in PSEUDO_EVENTS

    @staticmethod
    def is_class_property_change(event: QEvent) -> bool:
        """Return whether a DynamicPropertyChange event targets the CSS class property."""
        property_name = getattr(event, "propertyName", lambda: None)()
        return property_name is not None and getattr(property_name, "data", lambda: b"")() == b"class"

    @staticmethod
    def dispatch(engine: TransitionEngine, widget: QWidget, event: QEvent, event_type: QEvent.Type) -> None:
        """Route a relevant Qt event to the focused handler for that event."""
        if EventRouter.is_pseudo(event_type):
            EventRouter.handle_pseudo(engine, widget, event, event_type)
            return
        match event_type:
            case QEvent.Type.Polish:
                engine.on_polish(widget)
            case QEvent.Type.Resize:
                engine.on_resize(widget)
            case QEvent.Type.DynamicPropertyChange:
                if EventRouter.is_class_property_change(event):
                    engine.on_class_change(widget)
            case QEvent.Type.ParentChange:
                engine.on_parent_change(widget)
            case QEvent.Type.WindowActivate:
                engine.on_window_activate(widget)
            case QEvent.Type.WindowDeactivate:
                engine.on_window_deactivate(widget)
            case QEvent.Type.Leave if widget.isWindow():
                engine.on_window_deactivate(widget, clear_active=False)
            case _:
                pass

    @staticmethod
    def handle_pseudo(engine: TransitionEngine, widget: QWidget, event: QEvent, event_type: QEvent.Type) -> None:
        """Update one widget's pseudo-state and evaluate any resulting transition."""
        is_mouse_press = event_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
        if is_mouse_press and isinstance(event, QMouseEvent) and event.button() != Qt.MouseButton.LeftButton:
            if engine.left_click_only:
                return
            timestamp = event.timestamp()
            if timestamp == engine.claimed_mouse_event_ts or not engine.should_evaluate(widget):
                return
            engine.claimed_mouse_event_ts = timestamp
        ctx = engine.get_context(widget)
        updated = PseudoMachine.update(ctx.active_pseudos, event_type)
        cause = engine.prepare_clicked(widget, ctx, updated) if is_mouse_press else EvaluationCause.PSEUDO_STATE
        if updated == ctx.active_pseudos:
            return
        ctx.active_pseudos = updated
        engine.evaluate_widget_state(widget, cause=cause)
        if cause is EvaluationCause.CLICKED_ACTIVATION:
            engine.finish_clicked_activation(widget, ctx)
