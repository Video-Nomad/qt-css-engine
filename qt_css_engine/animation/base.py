"""Animation base — non-owning widget targets and shared steps reversal."""

import weakref

from qt_css_engine.animation.steps import steps_seek_ms
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject
from qt_css_engine.qt_compat.QtWidgets import QWidget


class WidgetAnimation(QObject):
    """Animations observe their widget; the application controls its lifetime."""

    def __init__(self, widget: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._widget_ref = weakref.ref(widget)

    @property
    def widget(self) -> QWidget:
        widget = self._widget_ref()
        if widget is None:
            raise RuntimeError("Animation widget has been collected")
        return widget


class StepsReversalMixin:
    """Mixin for steps() custom curves — retraces origin without jump."""

    def _should_reverse_steps(self, *, is_running: bool, target: object, origin: object | None) -> bool:
        # Generic check; subclasses provide curve check
        return bool(is_running and origin is not None and target == origin)

    @staticmethod
    def _seek_for_steps(duration: int, current_ms: int) -> int:
        seek, _ = steps_seek_ms(max(1, duration), current_ms)
        return seek

    @staticmethod
    def is_steps_curve(curve: QEasingCurve | QEasingCurve.Type) -> bool:
        if not isinstance(curve, QEasingCurve):
            return False
        return curve.type() == QEasingCurve.Type.Custom
