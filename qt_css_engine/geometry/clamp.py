"""Border-radius clamping — Qt caps radius at half the border rect."""

import math

from qt_css_engine.constants import BORDER_RADIUS_PROPS
from qt_css_engine.geometry.box_model import margin_side_px
from qt_css_engine.qt_compat.QtWidgets import QWidget


def _numeric_px(raw: str | None) -> float | None:
    from qt_css_engine.utils.parsing import parse_css_numeric

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
    from qt_css_engine.geometry.box_model import padding_side_px

    side_a, side_b = _axis_sides(axis)
    return padding_side_px(props, side_a) + padding_side_px(props, side_b)


def _axis_border_px(widget: QWidget, props: dict[str, str], axis: str) -> int:
    from qt_css_engine.geometry.box_model import total_border_px

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
    return float(math.floor(min(value, min(width, height) / 2.0)))
