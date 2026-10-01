"""Graphics-effect helpers — opacity / shadow slot arbitration."""

from qt_css_engine.constants import DISABLE_BOX_SHADOW_PROPERTY
from qt_css_engine.qt_compat.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget
from qt_css_engine.utils.color import ShadowParams


def is_box_shadow_disabled(widget: QWidget) -> bool:
    return widget.property(DISABLE_BOX_SHADOW_PROPERTY) is True


def apply_opacity_to_widget(widget: QWidget, value: float, priority: str) -> None:
    if is_box_shadow_disabled(widget):
        apply_shadow_to_widget(widget, None, priority)
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        if value >= 1.0:
            widget.setGraphicsEffect(None)
            desired: ShadowParams | None = getattr(widget, "_desired_shadow", None)
            if desired is not None:
                apply_shadow_to_widget(widget, desired, priority)
        else:
            effect.setOpacity(value)
        return
    if isinstance(effect, QGraphicsDropShadowEffect) and priority != "opacity":
        return
    if value < 1.0:
        op = QGraphicsOpacityEffect(widget)
        op.setOpacity(value)
        widget.setGraphicsEffect(op)


def apply_shadow_to_widget(widget: QWidget, params: ShadowParams | None, priority: str) -> None:
    if is_box_shadow_disabled(widget):
        params = None
    setattr(widget, "_desired_shadow", params)
    effect = widget.graphicsEffect()
    if params is None:
        if isinstance(effect, QGraphicsDropShadowEffect):
            widget.setGraphicsEffect(None)
        return
    if isinstance(effect, QGraphicsDropShadowEffect):
        effect.setOffset(params.offset_x, params.offset_y)
        effect.setBlurRadius(params.blur)
        effect.setColor(params.color)
        return
    if isinstance(effect, QGraphicsOpacityEffect) and priority != "box-shadow":
        return
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setOffset(params.offset_x, params.offset_y)
    shadow.setBlurRadius(params.blur)
    shadow.setColor(params.color)
    widget.setGraphicsEffect(shadow)


def update_shadow_ancestor(widget: QWidget) -> None:
    w = widget.parentWidget()
    while w is not None:
        if w.graphicsEffect() is not None:
            w.update()
            return
        w = w.parentWidget()
