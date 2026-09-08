"""Animator factory — dispatches prop → concrete animator."""

from collections.abc import Callable

from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.constants import SUPPORTED_NUMERIC_PROPS
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.utils.parsing import parse_css_numeric, parse_css_val

Animation = ColorAnimation | OpacityAnimation | GenericPropertyAnimation | BoxShadowHandle


def create_animator(
    widget: QWidget,
    prop: str,
    initial_raw: str,
    duration_ms: int,
    curve: QEasingCurve | QEasingCurve.Type,
    ctx: WidgetState | None = None,
    box_props: dict[str, str] | None = None,
    style_flush_callback: Callable[[QWidget, WidgetState], None] | None = None,
    effect_priority: str = "opacity",
    parent: QObject | None = None,
) -> Animation | None:
    """Instantiate correct animator for prop, or None if not animatable."""
    if "color" in prop:
        return ColorAnimation(widget, prop, initial_raw, duration_ms, curve, parent, ctx, style_flush_callback)
    if prop == "opacity":
        parsed = parse_css_val(initial_raw)
        initial = float(parsed) if isinstance(parsed, (int, float)) else 0.0
        return OpacityAnimation(widget, initial, duration_ms, curve, parent, effect_priority)
    if prop == "box-shadow":
        return BoxShadowHandle(widget, initial_raw, duration_ms, curve, parent, effect_priority)
    if prop in SUPPORTED_NUMERIC_PROPS:
        parsed = parse_css_numeric(initial_raw)
        if parsed is not None:
            val, unit = parsed
            return GenericPropertyAnimation(
                widget, prop, val, duration_ms, curve, parent, unit, ctx, box_props, style_flush_callback
            )
    return None
