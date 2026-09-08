"""Numeric animation — width/margin/padding/border-radius etc."""

from collections.abc import Callable

from qt_css_engine.animation.base import StepsReversalMixin
from qt_css_engine.constants import BORDER_RADIUS_PROPS, NON_NEGATIVE_PROPS, SIZE_PROPS
from qt_css_engine.geometry.clamp import clamp_border_radius
from qt_css_engine.qt_compat import is_qobject_alive
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.effects import update_shadow_ancestor
from qt_css_engine.style.writer import scoped_anim_style
from qt_css_engine.utils.parsing import parse_css_numeric


class GenericPropertyAnimation(StepsReversalMixin, QObject):
    def __init__(
        self,
        widget: QWidget,
        prop: str,
        initial_val: int | float,
        duration_ms: int,
        easing_curve: QEasingCurve | QEasingCurve.Type,
        parent: QObject | None = None,
        unit: str = "px",
        ctx: WidgetState | None = None,
        box_props: dict[str, str] | None = None,
        style_flush_callback: Callable[[QWidget, WidgetState], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.widget = widget
        self.prop = prop
        self.unit = unit
        self._ctx = ctx
        self._style_flush_callback = style_flush_callback
        self._box_props = dict(box_props or {})
        self.current_val = self._effective_anim_value(float(initial_val), unit)
        self._target_box_size: tuple[float, float] | None = None
        self.natural_val: float = float(initial_val)
        self._clean_on_finish = False
        self._anim_origin_val: float | None = self.current_val

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)
        self.anim.setStartValue(self.current_val)
        self.anim.setEndValue(self.current_val)
        self.anim.valueChanged.connect(self._on_tick)
        self.anim.finished.connect(self._on_finished)

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

    def _effective_anim_value(self, value: float, unit: str | None = None) -> float:
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
        resolved_unit = self.unit if unit is None else unit
        if self.prop in BORDER_RADIUS_PROPS:
            return clamp_border_radius(
                self.widget, self.prop, max(0.0, value), resolved_unit, self._box_props, box_size
            )
        return value

    def update_box_props(self, box_props: dict[str, str]) -> None:
        self._box_props = dict(box_props)

    def _write_current_style_value_if_needed(self, target_raw: str, box_size: tuple[float, float] | None) -> None:
        props = self._props
        parsed_target = parse_css_numeric(target_raw)
        target_needs_clamp = False
        if parsed_target is not None and self.prop in BORDER_RADIUS_PROPS:
            target_val, target_unit = parsed_target
            target_needs_clamp = self._effective_target_value(target_val, target_unit, box_size) != target_val
        written = max(0.0, self.current_val) if self.prop in NON_NEGATIVE_PROPS else self.current_val
        written = clamp_border_radius(self.widget, self.prop, written, self.unit, self._box_props, box_size)
        current_raw = props.get(self.prop)
        current_parsed = parse_css_numeric(current_raw)
        stored_matches = (
            current_parsed is not None and current_parsed[1] == self.unit and abs(current_parsed[0] - written) < 1e-6
        )
        if stored_matches or (current_raw is None and not target_needs_clamp):
            return
        props[self.prop] = f"{written:.3f}{self.unit}"
        self._request_style_flush(props, update_shadow=self.prop in SIZE_PROPS)

    def _on_tick(self, val: int | float | None) -> None:
        if not is_qobject_alive(self.widget):
            self.anim.stop()
            return
        if val is None:
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
            self.current_val = self._effective_target_value(val, box_size=final_box_size)
        else:
            self.current_val = self._effective_anim_value(val)
        written = max(0.0, self.current_val) if self.prop in NON_NEGATIVE_PROPS else self.current_val
        if final_box_size is not None:
            written = clamp_border_radius(self.widget, self.prop, written, self.unit, self._box_props, final_box_size)
        props = self._props
        props[self.prop] = f"{written:.3f}{self.unit}"
        self._request_style_flush(props, update_shadow=self.prop in SIZE_PROPS)

    def _flush_final_immediate(self, *, update_shadow: bool = False) -> None:
        """Flush the final frame synchronously so it paints same turn, not next."""
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

    def _on_finished(self) -> None:
        if self.prop in BORDER_RADIUS_PROPS and self._target_box_size is not None:
            try:
                self._on_tick(self.anim.endValue())
            except RuntimeError:
                pass
        self._target_box_size = None
        if not self._clean_on_finish:
            # Explicit target: last tick's value is already in the dict but may
            # still be pending batched flush — push it now so layout settles
            # same frame instead of showing penultimate for one extra paint.
            try:
                self._flush_final_immediate(update_shadow=self.prop in SIZE_PROPS)
            except RuntimeError:
                pass
            return
        self._clean_on_finish = False
        props = self._props
        if self.prop in props:
            del props[self.prop]
            try:
                self._flush_final_immediate(update_shadow=True)
            except RuntimeError:
                pass

    def update_spec(self, duration_ms: int, easing_curve: QEasingCurve) -> None:
        self.anim.setDuration(duration_ms)
        self.anim.setEasingCurve(easing_curve)

    def snap_to(self, value_raw: str, box_size: tuple[float, float] | None = None) -> None:
        self._target_box_size = None
        self.anim.stop()
        self._clean_on_finish = False
        parsed = parse_css_numeric(value_raw)
        if parsed is not None:
            raw_val, unit = parsed
            self.unit = unit
            self.current_val = self._effective_target_value(raw_val, unit, box_size)
            self._anim_origin_val = self.current_val
            written = max(0.0, self.current_val) if self.prop in NON_NEGATIVE_PROPS else self.current_val
            if self.prop in BORDER_RADIUS_PROPS:
                written = clamp_border_radius(
                    self.widget, self.prop, max(0.0, written), self.unit, self._box_props, box_size
                )
            self._props[self.prop] = f"{written:.3f}{self.unit}"

    def snap_to_natural(self) -> None:
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
        parsed = parse_css_numeric(target_raw)
        if parsed is None:
            return
        t_val = self._effective_target_value(parsed[0], parsed[1], box_size)
        is_running = self.anim.state() == self.anim.State.Running
        if is_running and t_val == self.anim.endValue():
            return
        if not is_running and abs(t_val - self.current_val) < 1e-6:
            self._target_box_size = box_size
            self._write_current_style_value_if_needed(target_raw, box_size)
            return
        self._target_box_size = box_size
        if self.is_steps_curve(self.anim.easingCurve()) and self._should_reverse_steps(
            is_running=is_running, target=t_val, origin=self._anim_origin_val
        ):
            dur = max(1, self.anim.duration())
            seek_ms = self._seek_for_steps(dur, self.anim.currentTime())
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
        self._on_tick(self.current_val)
