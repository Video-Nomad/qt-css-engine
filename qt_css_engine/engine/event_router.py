"""Event router — dispatch Qt events to their handlers."""

from typing import TYPE_CHECKING

from qt_css_engine.constants import DISABLE_BOX_SHADOW_PROPERTY, PSEUDO_EVENTS
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.handlers.class_change import handle_class_change
from qt_css_engine.engine.handlers.clicked import finish_clicked_activation, prepare_clicked
from qt_css_engine.engine.handlers.parent_change import handle_parent_change
from qt_css_engine.engine.handlers.window import handle_window_activate, handle_window_deactivate
from qt_css_engine.qt_compat.QtCore import QEvent, Qt
from qt_css_engine.qt_compat.QtGui import QMouseEvent
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.pseudo import PseudoMachine

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine


class EventRouter:
    """Stateless helper for TransitionEngine.eventFilter routing."""

    @staticmethod
    def changed_property_name(event: QEvent) -> str | None:
        """Return the dynamic-property name for a DynamicPropertyChange event, if any."""
        property_name = getattr(event, "propertyName", lambda: None)()
        if property_name is None:
            return None
        data = getattr(property_name, "data", lambda: b"")()
        if not data:
            return None
        try:
            return bytes(data).decode("utf-8", "ignore")
        except TypeError, ValueError:
            return None

    @staticmethod
    def dispatch(engine: TransitionEngine, widget: QWidget, event: QEvent, event_type: QEvent.Type) -> None:
        """Route a relevant Qt event to the focused handler for that event."""
        if event_type in PSEUDO_EVENTS:
            EventRouter.handle_pseudo(engine, widget, event, event_type)
            return
        match event_type:
            case QEvent.Type.Polish:
                engine.on_polish(widget)
            case QEvent.Type.Resize:
                engine.on_resize(widget)
            case QEvent.Type.DynamicPropertyChange:
                attr_name = EventRouter.changed_property_name(event)
                if attr_name == "class":
                    handle_class_change(engine, widget)
                elif attr_name is not None:
                    if attr_name == DISABLE_BOX_SHADOW_PROPERTY:
                        engine.evaluator.refresh_shadow_policy(widget)
                    if attr_name in engine.matcher.tracked_attrs:
                        handle_class_change(engine, widget)
            case QEvent.Type.ParentChange:
                handle_parent_change(engine, widget)
            case QEvent.Type.WindowActivate:
                handle_window_activate(engine, widget)
            case QEvent.Type.WindowDeactivate:
                handle_window_deactivate(engine, widget)
            case QEvent.Type.Leave if widget.isWindow():
                handle_window_deactivate(engine, widget, clear_active=False)
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
        cause = prepare_clicked(engine, widget, ctx, updated) if is_mouse_press else EvaluationCause.PSEUDO_STATE
        if updated == ctx.active_pseudos:
            return
        ctx.active_pseudos = updated
        engine.evaluate_widget_state(widget, cause=cause)
        if cause is EvaluationCause.CLICKED_ACTIVATION:
            finish_clicked_activation(engine, widget, ctx)
