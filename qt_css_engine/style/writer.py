"""Scoped stylesheet writer — batched flush with dedup."""

import itertools
from collections.abc import Callable

from qt_css_engine.constants import BORDER_RADIUS_PROPS
from qt_css_engine.geometry.clamp import clamp_border_radius, target_border_radius_box_size
from qt_css_engine.qt_compat.QtCore import QTimer
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.effects import update_shadow_ancestor
from qt_css_engine.utils.parsing import parse_css_numeric

_scope_counter = itertools.count(1)


def scoped_anim_style(widget: QWidget, props: dict[str, str]) -> str:
    selector: str | None = getattr(widget, "_anim_scope_selector", None)
    if selector is None:
        scope_id: str = widget.property("_anim_scope") or ""
        if not scope_id:
            scope_id = str(next(_scope_counter))
            widget.setProperty("_anim_scope", scope_id)
        selector = f'{type(widget).__name__}[_anim_scope="{scope_id}"]'
        setattr(widget, "_anim_scope_selector", selector)
    props_str = " ".join(f"{p}: {v};" for p, v in props.items())
    return f"{selector} {{ {props_str} }}"


class StyleWriter:
    """Owns scoped inline-style flush coalescing and applied-style dedup.

    Batching: animation ticks call schedule() which coalesces to one stylesheet write
    per event-loop turn via QTimer.singleShot(0). Class-change evaluation sets
    style_flush_immediate so its first frame is flushed once at the end of evaluation.
    """

    def __init__(self, get_ctx: Callable[[int], WidgetState | None] | None = None) -> None:
        self._get_ctx = get_ctx

    def bind(self, get_ctx: Callable[[int], WidgetState | None]) -> None:
        """Bind the widget-id -> context lookup used by deferred flushes."""
        self._get_ctx = get_ctx

    def schedule(self, widget: QWidget, ctx: WidgetState) -> None:
        """Queue one stylesheet write after the current burst of animation ticks."""
        # The evaluator commits the complete first frame synchronously. Writing here would
        # repolish once per property and expose partially updated box-model values to Qt.
        # No timer is needed: this batch is drained before evaluation returns.
        if ctx.style_flush_immediate:
            ctx.style_flush_pending = True
            return
        if ctx.style_flush_pending:
            return
        ctx.style_flush_pending = True
        wid = id(widget)
        get_ctx = self._get_ctx
        if get_ctx is None:
            QTimer.singleShot(0, lambda: self._flush_captured(widget, ctx))
        else:
            QTimer.singleShot(0, lambda: self.flush_scheduled(widget, wid, get_ctx))

    def _flush_captured(self, widget: QWidget, ctx: WidgetState) -> None:
        if not ctx.style_flush_pending:
            return
        try:
            self.flush_now(widget, ctx)
        except RuntimeError:
            ctx.style_flush_pending = False

    def flush_scheduled(
        self,
        widget: QWidget,
        wid: int,
        get_ctx: Callable[[int], WidgetState | None] | None = None,
    ) -> None:
        lookup = get_ctx or self._get_ctx
        ctx = lookup(wid) if lookup is not None else None
        if ctx is None or not ctx.style_flush_pending:
            return
        try:
            self.flush_now(widget, ctx)
        except RuntimeError:
            ctx.style_flush_pending = False

    def flush_now(self, widget: QWidget, ctx: WidgetState) -> None:
        """Normalize interdependent inline props and apply them as one scoped stylesheet."""
        ctx.style_flush_pending = False
        self.normalize(widget, ctx)
        style = scoped_anim_style(widget, ctx.css_anim_props)
        # setStyleSheet() re-parses the sheet and repolishes the widget and its children even
        # when the text is unchanged. Evaluations that resolve to the same inline style are
        # common (re-entered hover, polish sweeps, ticks that round to the same value), so
        # skipping the redundant write is the single cheapest saving on this path.
        # The context cache avoids a Qt getter on changing frames. The styleSheet() fallback
        # detects application code replacing the inline stylesheet between two equal frames.
        if style != ctx.applied_style or widget.styleSheet() != style:
            ctx.applied_style = style
            widget.setStyleSheet(style)
        update_shadow_ancestor(widget)

    def normalize(self, widget: QWidget, ctx: WidgetState) -> None:
        """Clamp radius values against the same pending box model that is about to be applied."""
        from qt_css_engine.animation.numeric import GenericPropertyAnimation

        props = ctx.css_anim_props
        # Resolving the target box size reads sizeHint() and re-parses the box model; skip it
        # entirely unless a radius is actually pending, which is the case on most flushes.
        pending_radii = props.keys() & BORDER_RADIUS_PROPS
        if not pending_radii:
            return
        box_props = {**ctx.style_box_props, **props}
        box_size = target_border_radius_box_size(widget, box_props)
        for prop in pending_radii:
            raw = props.get(prop)
            parsed = parse_css_numeric(raw)
            if parsed is None:
                continue
            value, unit = parsed
            animation = ctx.active_animations.get(prop)
            if isinstance(animation, GenericPropertyAnimation):
                # A previous flush may have clamped the sample to an earlier size.
                # Keep the animator's value so later size ticks can expose it again.
                value = animation.current_val
            clamped = clamp_border_radius(widget, prop, max(0.0, value), unit, box_props, box_size)
            if clamped != parsed[0]:
                props[prop] = f"{clamped:.3f}{unit}"
