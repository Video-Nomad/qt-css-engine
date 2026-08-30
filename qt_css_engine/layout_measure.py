from qt_css_engine.qt_compat.QtWidgets import QApplication, QFrame, QStyle, QWidget
from qt_css_engine.types import InternalWriteReason, WidgetContext
from qt_css_engine.utils import parse_css_val, scoped_anim_style

# Side -> longhand property name. These lookups run hundreds of times per animation frame
# (four sides per border-radius clamp), so the names are interned up front rather than
# rebuilt with an f-string on every call.
_SIDES = ("left", "right", "top", "bottom")
_PADDING_KEYS = {side: f"padding-{side}" for side in _SIDES}
_MARGIN_KEYS = {side: f"margin-{side}" for side in _SIDES}
_BORDER_WIDTH_KEYS = {side: f"border-{side}-width" for side in _SIDES}


def padding_side_px(base_props: dict[str, str], side: str) -> int:
    """Return the QSS padding in pixels for one side ('left', 'right', 'top', 'bottom')."""
    raw = base_props.get(_PADDING_KEYS.get(side, f"padding-{side}")) or base_props.get("padding") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def margin_side_px(base_props: dict[str, str], side: str) -> int:
    """Return the QSS margin in pixels for one side ('left', 'right', 'top', 'bottom')."""
    raw = base_props.get(_MARGIN_KEYS.get(side, f"margin-{side}")) or base_props.get("margin") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def _border_side_px(base_props: dict[str, str], side: str) -> int:
    """Return the QSS border width in pixels for one side ('left', 'right', 'top', 'bottom')."""
    raw = base_props.get(_BORDER_WIDTH_KEYS.get(side, f"border-{side}-width")) or base_props.get("border-width") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def total_border_px(widget: QWidget, base_props: dict[str, str], side: str) -> int:
    """
    Return the effective border width for one side, accounting for native platform borders.

    When any border property is present in QSS, QStyleSheetStyle controls border drawing and
    the QSS value is authoritative (may be 0 for 'border: none').
    When no border property is in QSS at all, the native platform border is used:
    - QFrame subclasses: use widget.frameWidth() — reflects the actual frame drawn (0 for NoFrame).
      PM_DefaultFrameWidth is a style-level metric that does not match the actual rendered frame for
      widgets like QLabel (NoFrame), causing the natural-size calculation to undercount by PM_DefaultFrameWidth
      per side and producing a snap at animation end.
    - Other widgets (QPushButton, QLineEdit, …): use PM_DefaultFrameWidth as before.

    Qt's contentsMargins() tracks only QSS padding, so contentsRect() = content + effective_border.
    Subtracting this value gives the true CSS content-box size that min-width/max-width operate on.
    """
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
    """
    Subtract border+padding+margin from a raw pixel size to get the content-area size.

    ``prop`` must contain ``"width"`` or ``"height"`` to select the axis.
    The result may be negative if box-model extras exceed ``pixel_value``; callers clamp as needed.

    For QFrame-derived widgets (QLabel, QFrame, …) Qt reflects the full QSS box-model
    (border + padding + margin) in widget.contentsRect(); we trust that delta directly.
    Reading the same values from base_props would double-count, because QLabel's frameWidth()
    already bakes padding+margin into the frame.

    For non-QFrame widgets (QPushButton, QLineEdit, …) contentsRect() does NOT reflect QSS
    padding/border, so we compute from the CSS values plus PM_DefaultFrameWidth for the
    native frame when no border is declared.

    Note: Qt's layout operates in integer logical pixels, so this calculation should be exact in practice
    even under fractional OS scaling.
    """
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


def get_preferred_size_fallback(widget: QWidget, base_props: dict[str, str], prop: str) -> str:
    """Return the widget's natural size as a CSS pixel value for the given size property."""
    hint = widget.sizeHint()
    px = hint.width() if "width" in prop else hint.height()
    return f"{max(0, content_box_px(widget, base_props, prop, px))}px"


def get_natural_size(widget: QWidget, ctx: WidgetContext, base_props: dict[str, str], prop: str) -> str:
    """
    Return the widget's unconstrained natural size for prop.

    Temporarily strips our inline size constraint, activates the parent layout so it
    redistributes space without the constraint, then reads widget.width()/height().
    This gives the true layout-assigned natural size (e.g. stretch-fill width), not
    just sizeHint() which only reflects the widget's intrinsic text/content size.

    A second layout.activate() in the finally block restores widget geometry to the
    constrained size so there is no visible flash before the animation begins.

    Falls back to sizeHint()-based measurement when the widget has no parent layout.
    """
    axis_props = {"width", "min-width", "max-width"} if "width" in prop else {"height", "min-height", "max-height"}
    constrained = {k for k in axis_props if k in ctx.css_anim_props}
    if not constrained:
        return get_preferred_size_fallback(widget, base_props, prop)
    stripped = {k: v for k, v in ctx.css_anim_props.items() if k not in constrained}
    parent = widget.parentWidget()
    parent_layout = parent.layout() if parent is not None else None

    # Collect ancestor layouts outermost-first.  When the inline size constraint is
    # stripped, activating only the immediate parent redistributes children within the
    # parent container's *current* (animation-inflated) size — sibling buttons expand
    # to fill the stale wide frame and we measure the wrong natural width.  Activating
    # from outermost to innermost lets each ancestor shrink to its unconstrained
    # sizeHint before the next level redistributes, giving the true natural width.
    ancestors: list[QWidget] = []
    if parent_layout is not None:
        w: QWidget | None = widget
        while w is not None:
            ancestors.append(w)
            w = w.parentWidget()
        ancestors.reverse()  # outermost first

    window = widget.window()
    was_updates_enabled = False
    if window is not None:
        was_updates_enabled = window.updatesEnabled()
        if was_updates_enabled:
            window.setUpdatesEnabled(False)

    ctx.internal_write_depth += 1
    ctx.internal_write_reason = InternalWriteReason.MEASURE
    try:
        widget.setStyleSheet(scoped_anim_style(widget, stripped))
        # setStyleSheet() calls style.polish() synchronously, which updates
        # widget.maximumWidth/minimumWidth.  activate() then assigns the natural size.
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
            result = f"{actual}px" if actual > 0 else get_preferred_size_fallback(widget, base_props, prop)
        else:
            result = get_preferred_size_fallback(widget, base_props, prop)
    finally:
        restored = scoped_anim_style(widget, ctx.css_anim_props)
        ctx.applied_style = restored
        widget.setStyleSheet(restored)
        # Restore the constrained geometry so there is no flash before animation starts.
        if parent_layout is not None:
            for w_ in ancestors:
                w_.updateGeometry()
                if (l_ := w_.layout()) is not None:
                    l_.invalidate()
            for w_ in ancestors:
                if (l_ := w_.layout()) is not None:
                    l_.activate()
        ctx.internal_write_depth -= 1
        if ctx.internal_write_depth == 0:
            ctx.internal_write_reason = None
        if was_updates_enabled and window is not None:
            window.setUpdatesEnabled(True)
    return result
