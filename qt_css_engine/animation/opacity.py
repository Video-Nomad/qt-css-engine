"""Opacity animation."""

from qt_css_engine.qt_compat import is_qobject_alive
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.style.effects import apply_opacity_to_widget
from qt_css_engine.utils.parsing import parse_css_val


class OpacityAnimation(QObject):
    def __init__(
        self,
        widget: QWidget,
        initial_val: float | int | str,
        duration_ms: int,
        easing_curve: QEasingCurve | QEasingCurve.Type,
        parent: QObject | None = None,
        effect_priority: str = "opacity",
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.effect_priority = effect_priority
        self._current_val = float(initial_val)
        apply_opacity_to_widget(widget, self._current_val, self.effect_priority)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.valueChanged.connect(self._on_tick)

    def _on_tick(self, val: float) -> None:
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
        self._current_val = val
        apply_opacity_to_widget(self.widget, val, self.effect_priority)

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        self.anim.stop()
        t_val = parse_css_val(value_raw)
        if isinstance(t_val, (int, float)):
            self._current_val = float(t_val)
            apply_opacity_to_widget(self.widget, self._current_val, self.effect_priority)

    def set_target(self, target_raw: str) -> bool:
        t_val = parse_css_val(target_raw)
        if not isinstance(t_val, (int, float)):
            return False
        target = float(t_val)
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and target == self.anim.endValue():
            return False
        if not is_running and abs(target - self._current_val) < 1e-6:
            return False
        self.anim.stop()
        self.anim.setStartValue(self._current_val)
        self.anim.setEndValue(target)
        self.anim.start()
        return True
