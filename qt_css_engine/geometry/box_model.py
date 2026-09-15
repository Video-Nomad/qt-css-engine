"""Box-model helpers — padding / margin / border → pixel deltas."""

from qt_css_engine.qt_compat.QtWidgets import QApplication, QFrame, QStyle, QWidget
from qt_css_engine.utils.parsing import parse_css_val

_SIDES = ("left", "right", "top", "bottom")
_PADDING_KEYS = {side: f"padding-{side}" for side in _SIDES}
_MARGIN_KEYS = {side: f"margin-{side}" for side in _SIDES}
_BORDER_WIDTH_KEYS = {side: f"border-{side}-width" for side in _SIDES}


def padding_side_px(base_props: dict[str, str], side: str) -> int:
    key = _PADDING_KEYS.get(side)
    raw = base_props.get(key if key is not None else f"padding-{side}") or base_props.get("padding") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def margin_side_px(base_props: dict[str, str], side: str) -> int:
    key = _MARGIN_KEYS.get(side)
    raw = base_props.get(key if key is not None else f"margin-{side}") or base_props.get("margin") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def _border_side_px(base_props: dict[str, str], side: str) -> int:
    key = _BORDER_WIDTH_KEYS.get(side)
    raw = base_props.get(key if key is not None else f"border-{side}-width") or base_props.get("border-width") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def total_border_px(widget: QWidget, base_props: dict[str, str], side: str) -> int:
    if any(k.startswith("border") for k in base_props):
        return _border_side_px(base_props, side)
    if isinstance(widget, QFrame):
        return max(0, widget.frameWidth())
    style = widget.style() or QApplication.style()
    if style is None:
        return 0
    fw = style.pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, widget)
    return max(0, fw)


def content_box_px(widget: QWidget, base_props: dict[str, str], prop: str, pixel_value: int) -> int:
    if isinstance(widget, QFrame):
        cr = widget.contentsRect()
        if "width" in prop:
            extras = widget.width() - cr.width()
        else:
            extras = widget.height() - cr.height()
        return pixel_value - extras
    if "width" in prop:
        b = total_border_px(widget, base_props, "left") + total_border_px(widget, base_props, "right")
        p = padding_side_px(base_props, "left") + padding_side_px(base_props, "right")
        m = margin_side_px(base_props, "left") + margin_side_px(base_props, "right")
    else:
        b = total_border_px(widget, base_props, "top") + total_border_px(widget, base_props, "bottom")
        p = padding_side_px(base_props, "top") + padding_side_px(base_props, "bottom")
        m = margin_side_px(base_props, "top") + margin_side_px(base_props, "bottom")
    return pixel_value - b - p - m
