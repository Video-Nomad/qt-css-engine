"""Pseudo-state machine — priority and event → pseudo mapping."""

from qt_css_engine.qt_compat.QtCore import QEvent


class PseudoMachine:
    """Tracks pseudo-class priority and updates pseudo sets from Qt events."""

    # Single source of cascade priority — higher wins; TransitionEngine passes this to CascadeEvaluator.
    priority: dict[str, int] = {
        "": 0,
        ":hover": 1,
        ":focus": 1,
        ":pressed": 2,
        ":checked": 1,
        ":clicked": 3,
        ":active": 1,
    }

    @staticmethod
    def update(pseudos: set[str], event_type: QEvent.Type) -> set[str]:
        updated = pseudos.copy()
        if event_type == QEvent.Type.HoverEnter:
            updated.add(":hover")
        elif event_type == QEvent.Type.HoverLeave:
            updated.discard(":hover")
        elif event_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            updated.add(":pressed")
        elif event_type == QEvent.Type.MouseButtonRelease:
            updated.discard(":pressed")
        elif event_type == QEvent.Type.FocusIn:
            updated.add(":focus")
        elif event_type == QEvent.Type.FocusOut:
            updated.discard(":focus")
        return updated
