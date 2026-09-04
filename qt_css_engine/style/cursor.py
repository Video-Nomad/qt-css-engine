"""Cursor handling."""

from qt_css_engine.constants import CURSOR_MAP
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.types import WidgetContext


def apply_cursor(widget: QWidget, ctx: WidgetContext, target_props: dict[str, str]) -> None:
    cursor_val = target_props.get("cursor")
    desired = cursor_val if cursor_val in CURSOR_MAP else None
    if desired == ctx.applied_cursor:
        return
    if desired is not None:
        widget.setCursor(CURSOR_MAP[desired])
    else:
        widget.unsetCursor()
    ctx.applied_cursor = desired
