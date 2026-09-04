"""Animation base — shared steps-reversal and spec handling."""

from qt_css_engine.animation.steps import steps_seek_ms
from qt_css_engine.qt_compat.QtCore import QEasingCurve


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
