import math

from qt_css_engine.constants import BORDER_RADIUS_PROPS, NON_NEGATIVE_PROPS, SIZE_PROPS

from .qt_compat import is_qobject_alive
from .qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation
from .qt_compat.QtGui import QColor
from .qt_compat.QtWidgets import QWidget
from .types import ShadowParams, WidgetContext
from .utils import (
    apply_opacity_to_widget,
    apply_shadow_to_widget,
    interpolate_oklab,
    lerp_shadow,
    margin_side_px,
    padding_side_px,
    parse_box_shadow,
    parse_color,
    parse_css_numeric,
    parse_css_val,
    scoped_anim_style,
    shadow_as_transparent,
    total_border_px,
    update_shadow_ancestor,
)


def _numeric_px(raw: str | None) -> float | None:
    parsed = parse_css_numeric(raw)
    if parsed is None:
        return None
    value, unit = parsed
    if unit != "px":
        return None
    return max(0.0, value)


def _axis_sides(axis: str) -> tuple[str, str]:
    return ("left", "right") if axis == "width" else ("top", "bottom")


def _axis_margin_px(props: dict[str, str], axis: str) -> int:
    side_a, side_b = _axis_sides(axis)
    return margin_side_px(props, side_a) + margin_side_px(props, side_b)


def _axis_padding_px(props: dict[str, str], axis: str) -> int:
    side_a, side_b = _axis_sides(axis)
    return padding_side_px(props, side_a) + padding_side_px(props, side_b)


def _axis_border_px(widget: QWidget, props: dict[str, str], axis: str) -> int:
    side_a, side_b = _axis_sides(axis)
    return total_border_px(widget, props, side_a) + total_border_px(widget, props, side_b)


def _target_content_axis_px(
    widget: QWidget,
    props: dict[str, str],
    axis: str,
    current_margin_box_px: float,
) -> float | None:
    explicit = _numeric_px(props.get(axis))
    if explicit is not None:
        return explicit

    min_px = _numeric_px(props.get(f"min-{axis}"))
    max_px = _numeric_px(props.get(f"max-{axis}"))
    if min_px is None and max_px is None:
        return None

    extras = _axis_margin_px(props, axis) + _axis_padding_px(props, axis) + _axis_border_px(widget, props, axis)
    current_content = max(0.0, current_margin_box_px - extras)
    target = current_content
    if min_px is not None:
        target = max(target, min_px)
    if max_px is not None:
        target = min(target, max_px)
    return max(0.0, target)


def _target_margin_box_axis_px(
    widget: QWidget,
    props: dict[str, str],
    axis: str,
    current_margin_box_px: float,
) -> float | None:
    content_px = _target_content_axis_px(widget, props, axis, current_margin_box_px)
    if content_px is None:
        return None
    extras = _axis_margin_px(props, axis) + _axis_padding_px(props, axis) + _axis_border_px(widget, props, axis)
    return content_px + extras


def target_border_radius_box_size(widget: QWidget, box_props: dict[str, str]) -> tuple[float, float] | None:
    """Return target margin-box size implied by target size props, if any."""
    width = widget.width()
    height = widget.height()
    if width <= 0 or height <= 0:
        hint = widget.sizeHint()
        if width <= 0:
            width = hint.width()
        if height <= 0:
            height = hint.height()

    target_width = _target_margin_box_axis_px(widget, box_props, "width", float(width))
    target_height = _target_margin_box_axis_px(widget, box_props, "height", float(height))
    if target_width is None and target_height is None:
        return None
    return (
        target_width if target_width is not None else float(width),
        target_height if target_height is not None else float(height),
    )


def clamp_border_radius(
    widget: QWidget,
    prop: str,
    value: float,
    unit: str,
    box_props: dict[str, str] | None = None,
    box_size: tuple[float, float] | None = None,
) -> float:
    """
    Clamp pixel border-radius values to Qt's maximum supported corner radius.

    Qt snaps radii above half of the painted border rect's smaller side back to square corners.
    QSS margin sits outside that painted rect, while padding and border stay inside it.
    """
    if unit != "px" or prop not in BORDER_RADIUS_PROPS:
        return value
    props = box_props or {}
    if box_size is not None:
        width, height = box_size
    else:
        width = widget.width()
        height = widget.height()
    if width <= 0 or height <= 0:
        hint = widget.sizeHint()
        if width <= 0:
            width = hint.width()
        if height <= 0:
            height = hint.height()
    width -= margin_side_px(props, "left") + margin_side_px(props, "right")
    height -= margin_side_px(props, "top") + margin_side_px(props, "bottom")
    if width <= 0 or height <= 0:
        return value
    return math.floor(min(value, min(width, height) / 2.0))


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
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
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

        is_steps = self.anim.easingCurve().type() == QEasingCurve.Type.Custom
        if (
            is_steps
            and is_running
            and self._anim_origin is not None
            and target == self._anim_origin
            and self._end is not None
        ):
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
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
        self.current_color = interpolate_oklab(self.start_color, self.end_color, t)
        props = self._props
        props[self.prop] = self.current_color.name(QColor.NameFormat.HexArgb)
        self.widget.setStyleSheet(scoped_anim_style(self.widget, props))
        if self.start_color.alpha() != 255 or self.end_color.alpha() != 255:
            update_shadow_ancestor(self.widget)

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
        if not is_running and target_color == self.current_color:
            return

        is_steps = self.anim.easingCurve().type() == QEasingCurve.Type.Custom
        if is_steps and is_running and self._anim_origin_color is not None and target_color == self._anim_origin_color:
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
        box_props: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.prop = prop
        self.unit = unit
        self._ctx = ctx
        self._box_props = dict(box_props or {})
        self.current_val = self._effective_anim_value(float(initial_val), unit)
        self._target_box_size: tuple[float, float] | None = None
        # Captured at creation time (before any inline constraint is applied).
        # Used as the animation target when returning to natural/unconstrained state,
        # so we never call sizeHint() while min-width/max-width are still active.
        self.natural_val: float = float(initial_val)
        self._clean_on_finish = False
        self._anim_origin_val: float | None = self.current_val

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.valueChanged.connect(self._on_tick)
        self.anim.finished.connect(self._on_finished)

        # Explicitly tick so the initial value is immediately written to the scoped stylesheet
        # TODO: test if needed
        # self._on_tick(self.current_val)

    @property
    def _props(self) -> dict[str, str]:
        """Live css_anim_props dict from context, or widget fallback for standalone use."""
        if self._ctx is not None:
            return self._ctx.css_anim_props
        props: dict[str, str] = getattr(self.widget, "_css_anim_props", {})
        return props

    def _effective_anim_value(self, value: float, unit: str | None = None) -> float:
        """Normalize values used as animation endpoints/current state for Qt-limited props."""
        resolved_unit = self.unit if unit is None else unit
        if self.prop in BORDER_RADIUS_PROPS:
            return clamp_border_radius(self.widget, self.prop, max(0.0, value), resolved_unit, self._box_props)
        return value

    def _effective_target_value(
        self,
        value: float,
        unit: str | None = None,
        box_size: tuple[float, float] | None = None,
    ) -> float:
        """Normalize animation targets for Qt-limited props."""
        resolved_unit = self.unit if unit is None else unit
        if self.prop in BORDER_RADIUS_PROPS:
            return clamp_border_radius(
                self.widget,
                self.prop,
                max(0.0, value),
                resolved_unit,
                self._box_props,
                box_size,
            )
        return value

    def update_box_props(self, box_props: dict[str, str]) -> None:
        """Update box-model props used for border-radius clamping."""
        self._box_props = dict(box_props)

    def _on_tick(self, val: int | float) -> None:
        """Write interpolated numeric value to css_anim_props and refresh the widget stylesheet."""
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
        final_box_size = (
            self._target_box_size
            if self.prop in BORDER_RADIUS_PROPS
            and self._target_box_size is not None
            and self.anim.currentTime() >= self.anim.duration()
            else None
        )
        if final_box_size is not None:
            self.current_val = self._effective_target_value(float(val), box_size=final_box_size)
        else:
            self.current_val = self._effective_anim_value(float(val))
        written = max(0.0, self.current_val) if self.prop in NON_NEGATIVE_PROPS else self.current_val
        written = clamp_border_radius(self.widget, self.prop, written, self.unit, self._box_props, final_box_size)
        props = self._props
        props[self.prop] = f"{written:.3f}{self.unit}"
        self.widget.setStyleSheet(scoped_anim_style(self.widget, props))
        if self.prop in SIZE_PROPS:
            update_shadow_ancestor(self.widget)

    def _on_finished(self) -> None:
        """Remove the inline size constraint when targeting the natural layout size."""
        if self.prop in BORDER_RADIUS_PROPS and self._target_box_size is not None:
            try:
                self._on_tick(float(self.anim.endValue()))
            except RuntimeError:
                pass
        self._target_box_size = None
        if not self._clean_on_finish:
            return
        self._clean_on_finish = False
        props = self._props
        if self.prop in props:
            del props[self.prop]
            try:
                self.widget.setStyleSheet(scoped_anim_style(self.widget, props))
                update_shadow_ancestor(self.widget)
            except RuntimeError:
                pass

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        """Update duration and easing curve without restarting the animation."""
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str, box_size: tuple[float, float] | None = None) -> None:
        """Instantly apply a CSS numeric value without animation."""
        self._target_box_size = None
        self.anim.stop()
        self._clean_on_finish = False
        parsed = parse_css_numeric(value_raw)
        if parsed is not None:
            raw_val, unit = parsed
            self.unit = unit
            self.current_val = self._effective_target_value(raw_val, unit, box_size)
            self._anim_origin_val = self.current_val
            written = self.current_val
            if self.prop in BORDER_RADIUS_PROPS:
                written = clamp_border_radius(
                    self.widget,
                    self.prop,
                    max(0.0, written),
                    self.unit,
                    self._box_props,
                    box_size,
                )
            self._props[self.prop] = f"{written:.3f}{self.unit}"

    def snap_to_natural(self) -> None:
        """Stop animation and remove this prop from css_anim_props (returns widget to natural layout)."""
        self._target_box_size = None
        self.anim.stop()
        self._clean_on_finish = False
        props = self._props
        if self.prop in props:
            del props[self.prop]

    def set_target(
        self,
        target_raw: str,
        clean_on_finish: bool = False,
        box_size: tuple[float, float] | None = None,
    ) -> None:
        """Start or re-target the animation toward a new CSS numeric value."""
        parsed = parse_css_numeric(target_raw)
        if parsed is None:
            return
        t_val = self._effective_target_value(float(parsed[0]), parsed[1], box_size)
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and t_val == self.anim.endValue():
            return
        if not is_running and abs(t_val - self.current_val) < 1e-6:
            return

        self._target_box_size = box_size
        # Specific for handling steps()
        is_steps = self.anim.easingCurve().type() == QEasingCurve.Type.Custom
        if is_steps and is_running and self._anim_origin_val is not None and abs(t_val - self._anim_origin_val) < 1e-6:
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
        # Explicitly tick so the initial value is immediately written to the scoped stylesheet
        # TODO: test if needed
        # self._on_tick(self.current_val)


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
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
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
