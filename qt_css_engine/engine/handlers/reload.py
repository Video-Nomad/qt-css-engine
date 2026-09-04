"""Reload handler — hot-reload CSS rules."""

from typing import TYPE_CHECKING

from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.handlers import lifecycle as lifecycle_handler
from qt_css_engine.qt_compat.QtCore import QTimer
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget
from qt_css_engine.types import WidgetContext

if TYPE_CHECKING:
    from qt_css_engine.css.model import StyleRule
    from qt_css_engine.engine.transition_engine import TransitionEngine


def collect_reload_widgets(contexts: dict[int, WidgetContext]) -> tuple[set[QWidget], set[int]]:
    """Return live widgets with animations plus ids owning inline styles."""
    animated_widgets: set[QWidget] = set()
    inline_widget_ids: set[int] = set()
    for wid, ctx in list(contexts.items()):
        if not ctx.active_animations:
            continue
        sample = next(iter(ctx.active_animations.values()))
        try:
            sample.widget.objectName()
            animated_widgets.add(sample.widget)
            if any(
                not isinstance(animation, (BoxShadowHandle, OpacityAnimation))
                for animation in ctx.active_animations.values()
            ):
                inline_widget_ids.add(wid)
        except RuntimeError:
            pass
    return animated_widgets, inline_widget_ids


def reload_rules(engine: TransitionEngine, rules: list[StyleRule]) -> None:
    """Hot-reload CSS rules, clearing old animations and engine-owned inline styles."""
    animated_widgets, inline_widget_ids = collect_reload_widgets(engine.store.contexts)
    for ctx in list(engine.store.contexts.values()):
        reset_context_for_reload(engine, ctx)
    engine.matcher.rules = rules
    engine.matcher.build_quick_filters()
    engine.matcher.clear_caches()
    animated_widget_ids = clear_reload_styles(engine, animated_widgets)
    effect_only_widgets = {widget for widget in animated_widgets if id(widget) not in inline_widget_ids}
    QTimer.singleShot(
        0,
        lambda: reeval_reload_widgets_deferred(engine, effect_only_widgets, animated_widget_ids),
    )


def reset_context_for_reload(engine: TransitionEngine, ctx: WidgetContext) -> None:
    """Discard transient animation state before the new rule set is installed."""
    lifecycle_handler.cancel_all_pending_delays(engine, ctx)
    lifecycle_handler.disconnect_finished_callbacks(ctx, ctx.class_anim_callbacks)
    lifecycle_handler.disconnect_finished_callbacks(ctx, ctx.clicked_anim_callbacks)
    ctx.class_anim_props.clear()
    ctx.clicked_anim_props.clear()
    ctx.active_pseudos.discard(":clicked")
    ctx.class_anim_gen += 1
    ctx.clicked_anim_gen += 1
    lifecycle_handler.stop_animations(engine, ctx, clear_effects=True)


def clear_reload_styles(engine: TransitionEngine, animated_widgets: set[QWidget]) -> set[int]:
    """Remove engine-owned inline styles and return the ids reset during the reload."""
    animated_widget_ids: set[int] = set()
    for widget in animated_widgets:
        try:
            animated_widget_ids.add(id(widget))
            ctx = engine.get_context(widget)
            ctx.css_anim_props.clear()
            ctx.active_pseudos.clear()
            ctx.applied_style = None
            widget.setStyleSheet("")
        except RuntimeError:
            pass
    app = QApplication.instance()
    if isinstance(app, QApplication):
        for widget in app.allWidgets():
            if id(widget) in animated_widget_ids:
                continue
            try:
                ctx = engine.store.contexts.get(id(widget))
                if ctx is not None and ctx.css_anim_props:
                    ctx.css_anim_props.clear()
                    ctx.applied_style = None
                    widget.setStyleSheet("")
            except RuntimeError:
                pass
    return animated_widget_ids


def reeval_reload_widgets_deferred(
    engine: TransitionEngine, effect_only_widgets: set[QWidget], prev_animated_ids: set[int]
) -> None:
    """Re-evaluate widgets that need engine-managed state after a hot-reload stylesheet change."""
    for widget in effect_only_widgets:
        try:
            widget.objectName()
            ctx = engine.store.contexts.get(id(widget))
            if ctx is not None and ctx.active_animations:
                continue
            if engine.should_evaluate(widget):
                engine.evaluate_widget_state(widget, cause=EvaluationCause.RULE_RELOAD)
            else:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass
    if not engine.matcher.index.flags.has_effect and not engine.matcher.index.flags.has_border_radius:
        return
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in app.allWidgets():
        ctx = engine.store.contexts.get(id(widget))
        if id(widget) in prev_animated_ids:
            continue
        if ctx is not None and ctx.active_animations:
            continue
        if engine.should_evaluate(widget):
            engine.evaluate_widget_state(widget, cause=EvaluationCause.RULE_RELOAD)
    if engine.matcher.index.flags.has_border_radius:
        QTimer.singleShot(0, lambda: reeval_border_radius_widgets_after_reload(engine))


def reeval_border_radius_widgets_after_reload(engine: TransitionEngine) -> None:
    """Re-evaluate border-radius widgets after reload Polish/layout has had one more event-loop turn."""
    if not engine.matcher.index.flags.has_border_radius:
        return
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in app.allWidgets():
        try:
            widget.objectName()
            if not engine.should_evaluate(widget):
                continue
            if not any(rule.has_border_radius_props for rule in engine.matcher.matching_rules(widget)):
                continue
            ctx = engine.store.contexts.get(id(widget))
            if ctx is not None and ctx.active_animations:
                continue
            engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        except RuntimeError:
            pass
