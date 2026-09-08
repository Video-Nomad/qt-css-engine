"""Natural size measurement — unconstrained layout size."""

from qt_css_engine.geometry.box_model import content_box_px
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.suppress import InternalWriteReason, suppress
from qt_css_engine.state.widget_state import WidgetState


def get_preferred_size_fallback(widget: QWidget, base_props: dict[str, str], prop: str) -> str:
    hint = widget.sizeHint()
    px = hint.width() if "width" in prop else hint.height()
    return f"{max(0, content_box_px(widget, base_props, prop, px))}px"


def get_natural_size(
    widget: QWidget,
    ctx: WidgetState,
    base_props: dict[str, str],
    prop: str,
    current_raw: str | None = None,
) -> str:
    # lazy import to break cycle if needed
    from qt_css_engine.style.writer import scoped_anim_style as _scoped

    restore_props = dict(ctx.css_anim_props)
    measure_props = dict(restore_props)
    if prop not in measure_props and current_raw not in (None, "", "auto"):
        measure_props[prop] = current_raw

    axis_props = {"width", "min-width", "max-width"} if "width" in prop else {"height", "min-height", "max-height"}
    constrained = {k for k in axis_props if k in measure_props}
    if not constrained:
        return get_preferred_size_fallback(widget, base_props, prop)
    stripped = {k: v for k, v in measure_props.items() if k not in constrained}
    parent = widget.parentWidget()
    parent_layout = parent.layout() if parent is not None else None

    ancestors: list[QWidget] = []
    if parent_layout is not None:
        w: QWidget | None = widget
        while w is not None:
            ancestors.append(w)
            w = w.parentWidget()
        ancestors.reverse()

    window = widget.window()
    was_updates_enabled = False
    if window is not None:
        was_updates_enabled = window.updatesEnabled()
        if was_updates_enabled:
            window.setUpdatesEnabled(False)

    was_result: str | None = None
    try:
        with suppress(ctx, InternalWriteReason.MEASURE):
            widget.setStyleSheet(_scoped(widget, stripped))
            if parent_layout is not None:
                for w_ in ancestors:
                    w_.updateGeometry()
                    if (l_ := w_.layout()) is not None:
                        l_.invalidate()
                for w_ in ancestors:
                    if (l_ := w_.layout()) is not None:
                        l_.activate()
                raw_px = widget.width() if "width" in prop else widget.height()
                actual = content_box_px(widget, base_props, prop, raw_px)
                was_result = f"{actual}px" if actual > 0 else get_preferred_size_fallback(widget, base_props, prop)
            else:
                was_result = get_preferred_size_fallback(widget, base_props, prop)
            # Restore inside suppress so the Polish triggered by setStyleSheet is ignored
            restored = _scoped(widget, restore_props)
            ctx.applied_style = restored
            widget.setStyleSheet(restored)
            if parent_layout is not None:
                for w_ in ancestors:
                    w_.updateGeometry()
                    if (l_ := w_.layout()) is not None:
                        l_.invalidate()
                for w_ in ancestors:
                    if (l_ := w_.layout()) is not None:
                        l_.activate()
            result = was_result
    finally:
        if was_updates_enabled and window is not None:
            window.setUpdatesEnabled(True)
    return result
