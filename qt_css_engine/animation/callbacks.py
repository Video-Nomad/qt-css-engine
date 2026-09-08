"""Generation counters for class/clicked anims.

Stale finished-callbacks capture the gen at wire time and no-op when it changed.
"""

from qt_css_engine.state.widget_state import WidgetState


def next_class_gen(ctx: WidgetState) -> int:
    """Advance the class-change generation — stale finished callbacks become no-ops."""
    ctx.class_anim_gen += 1
    ctx.class_anim_props.clear()
    return ctx.class_anim_gen


def next_clicked_gen(ctx: WidgetState, props: set[str]) -> int:
    """Advance the :clicked generation and seed the props pending forward animation."""
    ctx.clicked_anim_gen += 1
    ctx.clicked_anim_props = set(props)
    return ctx.clicked_anim_gen
