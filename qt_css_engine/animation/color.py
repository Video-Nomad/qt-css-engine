"""Color animation — OKLab interpolation."""

from collections.abc import Callable

from qt_css_engine.animation.base import StepsReversalMixin
from qt_css_engine.qt_compat import is_qobject_alive
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.style.effects import update_shadow_ancestor
from qt_css_engine.style.writer import scoped_anim_style
from qt_css_engine.types import WidgetContext
from qt_css_engine.utils.color import lerp_oklab_premul, parse_color, to_oklab_premul


class ColorAnimation(StepsReversalMixin, QObject):
    def __init__(
        self,
        widget: QWidget,
        prop: str,
        initial_raw: str | QColor,
        duration_ms: int,
        easing_curve: QEasingCurve | QEasingCurve.Type,
        parent: QObject | None = None,
        ctx: WidgetContext | None = None,
        style_flush_callback: Callable[[QWidget, WidgetContext], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.prop = prop
        self._ctx = ctx
        self._style_flush_callback = style_flush_callback
        self.current_color = parse_color(initial_raw) if isinstance(initial_raw, str) else QColor(initial_raw)
        self.start_color = self.current_color
        self.end_color = self.current_color
        self._anim_origin_color: QColor | None = QColor(self.current_color)
        self._start_oklab = to_oklab_premul(self.start_color)
        self._end_oklab = self._start_oklab

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.valueChanged.connect(self._on_tick)
        self.anim.finished.connect(self._on_finished)

    def _sync_endpoints(self) -> None:
        self._start_oklab = to_oklab_premul(self.start_color)
        self._end_oklab = to_oklab_premul(self.end_color)

    @property
    def _props(self) -> dict[str, str]:
        if self._ctx is not None:
            return self._ctx.css_anim_props
        props: dict[str, str] = getattr(self.widget, "_css_anim_props", {})
        return props

    def _request_style_flush(self, props: dict[str, str], *, update_shadow: bool = False) -> None:
        if self._ctx is not None and self._style_flush_callback is not None:
            self._style_flush_callback(self.widget, self._ctx)
            return
        self.widget.setStyleSheet(scoped_anim_style(self.widget, props))
        if update_shadow:
            update_shadow_ancestor(self.widget)

    def _on_tick(self, t: float) -> None:
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
        self.current_color = lerp_oklab_premul(self._start_oklab, self._end_oklab, t)
        props = self._props
        props[self.prop] = self.current_color.name(QColor.NameFormat.HexArgb)
        self._request_style_flush(props, update_shadow=self.start_color.alpha() != 255 or self.end_color.alpha() != 255)

    def _on_finished(self) -> None:
        """Flush the final color synchronously so it doesn't lag one frame"""
        props = self._props
        ctx = self._ctx
        if ctx is None:
            try:
                self.widget.setStyleSheet(scoped_anim_style(self.widget, props))
                update_shadow_ancestor(self.widget)
            except RuntimeError:
                pass
            return
        ctx.style_flush_pending = False
        try:
            style = scoped_anim_style(self.widget, props)
            if style != ctx.applied_style or self.widget.styleSheet() != style:
                ctx.applied_style = style
                self.widget.setStyleSheet(style)
            update_shadow_ancestor(self.widget)
        except RuntimeError:
            pass

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        self.anim.stop()
        self.current_color = parse_color(value_raw)
        self.start_color = self.current_color
        self.end_color = self.current_color
        self._sync_endpoints()
        self._anim_origin_color = QColor(self.current_color)
        self._props[self.prop] = self.current_color.name(QColor.NameFormat.HexArgb)

    def set_target(self, target_raw: str) -> None:
        target_color = parse_color(target_raw)
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and target_color == self.end_color:
            return
        if not is_running and target_color == self.current_color:
            return
        if self.is_steps_curve(self.anim.easingCurve()) and self._should_reverse_steps(
            is_running=is_running, target=target_color, origin=self._anim_origin_color
        ):
            dur = max(1, self.anim.duration())
            seek_ms = self._seek_for_steps(dur, self.anim.currentTime())
            old_end = QColor(self.end_color)
            self._anim_origin_color = old_end
            self.start_color = old_end
            self.end_color = target_color
            self._sync_endpoints()
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
            self.anim.setCurrentTime(seek_ms)
            self._on_tick(self.anim.easingCurve().valueForProgress(min(seek_ms, dur) / dur))
            return
        self._anim_origin_color = QColor(self.current_color)
        self.start_color = self.current_color
        self.end_color = target_color
        self._sync_endpoints()
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self._on_tick(self.anim.easingCurve().valueForProgress(0.0))
