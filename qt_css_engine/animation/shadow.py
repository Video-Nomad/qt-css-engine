"""Shadow animation — delegates to geometry/clamp and style/effects."""

from qt_css_engine.animation.base import StepsReversalMixin
from qt_css_engine.qt_compat import is_qobject_alive
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.style.effects import apply_shadow_to_widget
from qt_css_engine.types import ShadowParams
from qt_css_engine.utils.color import lerp_shadow, parse_box_shadow, shadow_as_transparent


class BoxShadowHandle(StepsReversalMixin, QObject):
    def __init__(
        self,
        widget: QWidget,
        initial_raw: str,
        duration_ms: int,
        easing_curve: QEasingCurve | QEasingCurve.Type,
        parent: QObject | None = None,
        effect_priority: str = "opacity",
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.effect_priority = effect_priority
        self._current: ShadowParams | None = parse_box_shadow(initial_raw)
        self._start: ShadowParams | None = None
        self._end: ShadowParams | None = None
        self._anim_origin: ShadowParams | None = self._current
        apply_shadow_to_widget(widget, self._current, self.effect_priority)

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._on_tick)
        self.anim.finished.connect(self._on_finished)

    def _on_tick(self, t: float) -> None:
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
        start = self._start
        end = self._end
        if start is None:
            if end is None:
                return
            start = shadow_as_transparent(end)
        if end is None:
            end = shadow_as_transparent(start)
        self._current = lerp_shadow(start, end, t)
        apply_shadow_to_widget(self.widget, self._current, self.effect_priority)

    def _on_finished(self) -> None:
        self._current = self._end
        self._start = None
        try:
            apply_shadow_to_widget(self.widget, self._current, self.effect_priority)
        except RuntimeError:
            pass

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        self.anim.stop()
        self._current = parse_box_shadow(value_raw)
        self._start = None
        self._end = None
        self._anim_origin = self._current
        apply_shadow_to_widget(self.widget, self._current, self.effect_priority)

    def set_target(self, target_raw: str) -> None:
        target = parse_box_shadow(target_raw)
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and target == self._end:
            return
        if not is_running and target == self._current:
            return
        if (
            self.is_steps_curve(self.anim.easingCurve())
            and self._should_reverse_steps(is_running=is_running, target=target, origin=self._anim_origin)
            and self._end is not None
        ):
            dur = max(1, self.anim.duration())
            seek_ms = self._seek_for_steps(dur, self.anim.currentTime())
            old_end = self._end
            self._anim_origin = old_end
            self._start = old_end
            self._end = target
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
            self.anim.setCurrentTime(seek_ms)
            self._on_tick(self.anim.easingCurve().valueForProgress(min(seek_ms, dur) / dur))
            return
        self._anim_origin = self._current
        self._start = self._current
        self._end = target
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self._on_tick(self.anim.easingCurve().valueForProgress(0.0))
