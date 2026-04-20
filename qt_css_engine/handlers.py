from qt_css_engine.constants import NON_NEGATIVE_PROPS

from .qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation
from .qt_compat.QtGui import QColor
from .qt_compat.QtWidgets import QWidget
from .types import ShadowParams, WidgetContext
from .utils import (
    apply_opacity_to_widget,
    apply_shadow_to_widget,
    interpolate_oklab,
    lerp_shadow,
    parse_box_shadow,
    parse_color,
    parse_css_numeric,
    parse_css_val,
    scoped_anim_style,
    shadow_as_transparent,
)


class BoxShadowHandle(QObject):
    """
    Animates box-shadow via a QGraphicsDropShadowEffect on the widget.

    Stored in active_animations like other animation types so orphan cleanup (class
    removal, rule changes) correctly clears the shadow. Transitions between shadow
    states — including None (no shadow) — interpolate all fields and alpha.
    """

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
        """Interpolate the shadow between _start and _end at progress t and apply it."""
        # Resolve None as a transparent copy of the other end for fade in/out.
        if self._start is None and self._end is None:
            return
        start = (
            self._start if self._start is not None else shadow_as_transparent(self._end)  # type: ignore
        )
        end = (
            self._end if self._end is not None else shadow_as_transparent(self._start)  # type: ignore
        )
        self._current = lerp_shadow(start, end, t)
        apply_shadow_to_widget(self.widget, self._current, self.effect_priority)

    def _on_finished(self) -> None:
        """Snap to exact target on completion to clear floating-point residuals."""
        self._current = self._end
        self._start = None
        try:
            apply_shadow_to_widget(self.widget, self._current, self.effect_priority)
        except RuntimeError:
            pass

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        """Update duration and easing curve without restarting the animation."""
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        """Instantly apply a CSS box-shadow value without animation."""
        self.anim.stop()
        self._current = parse_box_shadow(value_raw)
        self._start = None
        self._end = None
        self._anim_origin = self._current
        apply_shadow_to_widget(self.widget, self._current, self.effect_priority)

    def set_target(self, target_raw: str) -> None:
        """Start or re-target the animation toward a new CSS box-shadow value."""
        target = parse_box_shadow(target_raw)

        is_running = self.anim.state() == self.anim.State.Running
        if is_running and target == self._end:
            return
        if not is_running and target == self._current:
            return

        if is_running and self._anim_origin is not None and target == self._anim_origin and self._end is not None:
            # Reversing to origin — swap start/end and seek so steps() retraces original path.
            dur = max(1, self.anim.duration())
            raw_p = min(self.anim.currentTime(), dur) / dur
            seek_ms = int((1.0 - raw_p) * dur)
            old_end = self._end
            self._anim_origin = old_end
            self._start = old_end
            self._end = target
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
            self.anim.setCurrentTime(seek_ms)
            # Force tick: step-start always returns 1.0 so Qt suppresses valueChanged.
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


class ColorAnimation(QObject):
    """
    Animates QSS color properties (background-color, color, border-color, etc.).
    Uses OKLab interpolation for perceptually uniform color transitions.
    """

    def __init__(
        self,
        widget: QWidget,
        prop: str,
        initial_raw: str | QColor,
        duration_ms: int,
        easing_curve: QEasingCurve | QEasingCurve.Type,
        parent: QObject | None = None,
        ctx: WidgetContext | None = None,
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.prop = prop
        self._ctx = ctx
        self.current_color = parse_color(initial_raw) if isinstance(initial_raw, str) else QColor(initial_raw)
        self.start_color = self.current_color
        self.end_color = self.current_color
        self._anim_origin_color: QColor | None = QColor(self.current_color)

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.valueChanged.connect(self._on_tick)

    @property
    def _props(self) -> dict[str, str]:
        """Live css_anim_props dict from context, or widget fallback for standalone use."""
        if self._ctx is not None:
            return self._ctx.css_anim_props
        # Fallback for standalone usage (tests without engine context).
        props: dict[str, str] = getattr(self.widget, "_css_anim_props", {})
        return props

    def _on_tick(self, t: float) -> None:
        """Write interpolated color to css_anim_props and refresh the widget stylesheet."""
        self.current_color = interpolate_oklab(self.start_color, self.end_color, t)
        props = self._props
        props[self.prop] = self.current_color.name(QColor.NameFormat.HexArgb)
        self.widget.setStyleSheet(scoped_anim_style(self.widget, props))

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        """Update duration and easing curve without restarting the animation."""
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        """Instantly apply a CSS color value without animation."""
        self.anim.stop()
        self.current_color = parse_color(value_raw)
        self.start_color = self.current_color
        self.end_color = self.current_color
        self._anim_origin_color = QColor(self.current_color)
        self._props[self.prop] = self.current_color.name(QColor.NameFormat.HexArgb)

    def set_target(self, target_raw: str) -> None:
        """Start or re-target the animation toward a new CSS color value."""
        target_color = parse_color(target_raw)
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and target_color == self.end_color:
            return

        if is_running and self._anim_origin_color is not None and target_color == self._anim_origin_color:
            # Reversing to origin — swap start/end and seek so steps() retraces original path.
            dur = max(1, self.anim.duration())
            raw_p = min(self.anim.currentTime(), dur) / dur
            seek_ms = int((1.0 - raw_p) * dur)
            old_end = QColor(self.end_color)
            self._anim_origin_color = old_end
            self.start_color = old_end
            self.end_color = target_color
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
            self.anim.setCurrentTime(seek_ms)
            # Force tick: step-start always returns 1.0 so Qt suppresses valueChanged
            # (new value == previous value from forward trip) — manually ensure the color updates.
            self._on_tick(self.anim.easingCurve().valueForProgress(min(seek_ms, dur) / dur))
            return

        self._anim_origin_color = QColor(self.current_color)
        self.start_color = self.current_color
        self.end_color = target_color
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        # Force first frame: Qt suppresses valueChanged when eased value at t=0 equals the
        # previous currentValue. step-start (n=1) always returns 1.0, so forward and return
        # trips both produce 1.0 — the signal is silenced on the return and the color never
        # updates. Explicit tick ensures current_color is applied regardless.
        self._on_tick(self.anim.easingCurve().valueForProgress(0.0))


class GenericPropertyAnimation(QObject):
    """
    Animates numeric QSS properties (widths, heights, margins, padding, etc.).
    Writes interpolated values directly into the widget's scoped inline stylesheet.
    """

    def __init__(
        self,
        widget: QWidget,
        prop: str,
        initial_val: int | float,
        duration_ms: int,
        easing_curve: QEasingCurve | QEasingCurve.Type,
        parent: QObject | None = None,
        unit: str = "px",
        ctx: WidgetContext | None = None,
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.prop = prop
        self.unit = unit
        self._ctx = ctx
        self.current_val = float(initial_val)
        # Captured at creation time (before any inline constraint is applied).
        # Used as the animation target when returning to natural/unconstrained state,
        # so we never call sizeHint() while min-width/max-width are still active.
        self.natural_val: float = float(initial_val)
        self._clean_on_finish = False
        self._anim_origin_val: float | None = float(initial_val)

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.valueChanged.connect(self._on_tick)
        self.anim.finished.connect(self._on_finished)

    @property
    def _props(self) -> dict[str, str]:
        """Live css_anim_props dict from context, or widget fallback for standalone use."""
        if self._ctx is not None:
            return self._ctx.css_anim_props
        props: dict[str, str] = getattr(self.widget, "_css_anim_props", {})
        return props

    def _on_tick(self, val: int | float) -> None:
        """Write interpolated numeric value to css_anim_props and refresh the widget stylesheet."""
        self.current_val = float(val)
        written = max(0.0, self.current_val) if self.prop in NON_NEGATIVE_PROPS else self.current_val
        props = self._props
        props[self.prop] = f"{written:.3f}{self.unit}"
        self.widget.setStyleSheet(scoped_anim_style(self.widget, props))

    def _on_finished(self) -> None:
        """Remove the inline size constraint when targeting the natural layout size."""
        if not self._clean_on_finish:
            return
        self._clean_on_finish = False
        props = self._props
        if self.prop in props:
            del props[self.prop]
            try:
                self.widget.setStyleSheet(scoped_anim_style(self.widget, props))
            except RuntimeError:
                pass

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        """Update duration and easing curve without restarting the animation."""
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        """Instantly apply a CSS numeric value without animation."""
        self.anim.stop()
        self._clean_on_finish = False
        parsed = parse_css_numeric(value_raw)
        if parsed is not None:
            self.current_val = parsed[0]
            self._anim_origin_val = self.current_val
            self._props[self.prop] = f"{self.current_val:.3f}{self.unit}"

    def snap_to_natural(self) -> None:
        """Stop animation and remove this prop from css_anim_props (returns widget to natural layout)."""
        self.anim.stop()
        self._clean_on_finish = False
        props = self._props
        if self.prop in props:
            del props[self.prop]

    def set_target(self, target_raw: str, clean_on_finish: bool = False) -> None:
        """Start or re-target the animation toward a new CSS numeric value."""
        parsed = parse_css_numeric(target_raw)
        if parsed is None:
            return
        t_val = float(parsed[0])
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and t_val == self.anim.endValue():
            return

        if is_running and self._anim_origin_val is not None and abs(t_val - self._anim_origin_val) < 1e-6:
            # Reversing to origin — swap start/end and seek so steps() retraces original path.
            dur = max(1, self.anim.duration())
            raw_p = min(self.anim.currentTime(), dur) / dur
            seek_ms = int((1.0 - raw_p) * dur)
            old_end = float(self.anim.endValue())
            self._anim_origin_val = old_end
            self._clean_on_finish = clean_on_finish
            self.anim.stop()
            self.anim.setStartValue(old_end)
            self.anim.setEndValue(t_val)
            self.anim.start()
            self.anim.setCurrentTime(seek_ms)
            return

        self._anim_origin_val = self.current_val
        self._clean_on_finish = clean_on_finish
        self.anim.stop()
        self.anim.setStartValue(self.current_val)
        self.anim.setEndValue(t_val)
        self.anim.start()


class OpacityAnimation(QObject):
    """Animates widget opacity via a QGraphicsOpacityEffect."""

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
        """Apply interpolated opacity to the widget's QGraphicsOpacityEffect."""
        self._current_val = val
        apply_opacity_to_widget(self.widget, val, self.effect_priority)

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        """Update duration and easing curve without restarting the animation."""
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str) -> None:
        """Instantly apply a CSS opacity value without animation."""
        self.anim.stop()
        t_val = parse_css_val(value_raw)
        if isinstance(t_val, (int, float)):
            self._current_val = float(t_val)
            apply_opacity_to_widget(self.widget, self._current_val, self.effect_priority)

    def set_target(self, target_raw: str) -> None:
        """Start or re-target the animation toward a new opacity value."""
        t_val = parse_css_val(target_raw)
        if not isinstance(t_val, (int, float)):
            return

        self.anim.stop()
        self.anim.setStartValue(self._current_val)
        self.anim.setEndValue(float(t_val))
        self.anim.start()
