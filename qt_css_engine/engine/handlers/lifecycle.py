"""Widget lifecycle — destroy teardown and animation release."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.effects import apply_shadow_to_widget
from qt_css_engine.utils.qt_helpers import safe_disconnect

if TYPE_CHECKING:
    from qt_css_engine.engine.transition_engine import TransitionEngine

event_logger = logging.getLogger("qt_css_engine.event")


def on_widget_destroyed(engine: TransitionEngine, widget: object) -> None:
    """Remove all engine state for a destroyed widget and stop its animations."""
    wid = id(widget)
    engine.connected_checkable_ids.discard(wid)
    engine.matcher.invalidate_widget_id(wid)
    engine.active_rule_widgets.pop(wid, None)
    engine.store.widgets.pop(wid, None)
    ctx = engine.store.contexts.pop(wid, None)
    if ctx is None:
        return
    event_logger.debug("On widget destroyed: %s, %s", type(widget), wid)
    cancel_all_pending_delays(engine, ctx)
    disconnect_finished_callbacks(ctx, ctx.class_anim_callbacks)
    disconnect_finished_callbacks(ctx, ctx.clicked_anim_callbacks)
    ctx.class_anim_props.clear()
    ctx.clicked_anim_props.clear()
    stop_animations(engine, ctx)


def cancel_all_pending_delays(engine: TransitionEngine, ctx: WidgetState) -> None:
    """Cancel every delayed transition currently held by a widget."""
    engine.delays.cancel_all(ctx)


def disconnect_finished_callbacks(ctx: WidgetState, callbacks: dict[str, Callable[[], None]]) -> None:
    """Disconnect callbacks held for animations that are about to be discarded."""
    for prop, callback in callbacks.items():
        anim_obj = ctx.active_animations.get(prop)
        if anim_obj is not None:
            try:
                safe_disconnect(anim_obj.anim.finished, callback)
            except RuntimeError, TypeError:
                pass
    callbacks.clear()


def stop_animations(engine: TransitionEngine, ctx: WidgetState, *, clear_effects: bool = False) -> None:
    """Stop and release all animation objects in a widget context."""
    for anim_obj in ctx.active_animations.values():
        try:
            anim_obj.anim.stop()
            if clear_effects:
                if isinstance(anim_obj, BoxShadowHandle):
                    apply_shadow_to_widget(anim_obj.widget, None, engine.effect_priority)
                elif isinstance(anim_obj, OpacityAnimation):
                    anim_obj.widget.setGraphicsEffect(None)
            anim_obj.deleteLater()
        except RuntimeError:
            pass
    ctx.active_animations.clear()
