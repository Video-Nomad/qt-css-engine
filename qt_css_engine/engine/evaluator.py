"""Evaluation pipeline — collect -> resolve -> animate/snap -> cleanup -> flush.

WidgetEvaluator owns the per-widget evaluation core. It operates on a single
Evaluation bundle (widget + ctx + state + cause) shared by every pipeline stage.

Pipeline shape per property (see apply_prop):
    resolve current/target -> color snap? -> natural noop? -> untransitioned snap?
    -> policy snap? -> animate (or hold for delay)

Sections below follow that order: entry points, per-property decision, value
resolution, animation run, orphan cleanup, then small value/snap helpers.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.factory import Animation, create_animator
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.constants import BORDER_RADIUS_PROPS, EFFECT_PROPS, SIZE_PROPS
from qt_css_engine.engine.evaluation import EvaluationCause, ResolvedProperty, ResolvedRuleState
from qt_css_engine.geometry.box_model import content_box_px
from qt_css_engine.geometry.clamp import clamp_border_radius, target_border_radius_box_size
from qt_css_engine.geometry.natural_size import get_natural_size, get_preferred_size_fallback
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEasingCurve
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.cursor import apply_cursor as apply_cursor_style
from qt_css_engine.style.effects import apply_shadow_to_widget
from qt_css_engine.utils.color import parse_color
from qt_css_engine.utils.easing import resolve_easing_curve
from qt_css_engine.utils.parsing import parse_css_numeric
from qt_css_engine.utils.qt_helpers import safe_disconnect

if TYPE_CHECKING:
    from qt_css_engine.css.model import TransitionSpec
    from qt_css_engine.engine.transition_engine import TransitionEngine

event_logger = logging.getLogger("qt_css_engine.event")


@dataclass
class Evaluation:
    """Single evaluation pass — the unit of work pushed through the pipeline."""

    widget: QWidget
    ctx: WidgetState
    state: ResolvedRuleState
    cause: EvaluationCause


class WidgetEvaluator:
    """Per-widget evaluation pipeline bound to its owning TransitionEngine."""

    def __init__(self, engine: TransitionEngine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Pipeline entry points
    # ------------------------------------------------------------------

    def evaluate(self, widget: QWidget, cause: EvaluationCause = EvaluationCause.DIRECT) -> None:
        """Evaluate all animated CSS properties and start, update, or snap animations."""
        if not self._engine.should_evaluate(widget):
            return
        ctx = self._engine.get_context(widget)
        if self.can_skip_initial_evaluation(widget, ctx, cause):
            return
        state = self._collect_state(widget, ctx)
        saved_immediate = ctx.style_flush_immediate
        ctx.style_flush_immediate = saved_immediate or cause.is_class_driven
        try:
            ev = Evaluation(widget, ctx, state, cause)
            needs_style_update = self._apply_animated_props(ev)
            if self.cleanup_orphans(ctx, state):
                needs_style_update = True
            if needs_style_update:
                event_logger.debug("Updating style: %s", widget)
                self._engine.writer.flush_now(widget, ctx)
            apply_cursor_style(widget, ctx, state.target_props)
        finally:
            ctx.style_flush_immediate = saved_immediate

    def fire_delayed_prop(self, widget: QWidget, prop: str) -> None:
        """Re-evaluate a single property after its transition-delay has elapsed."""
        if not self._engine.should_evaluate(widget):
            return
        ctx = self._engine.get_context(widget)
        state = self._collect_state(widget, ctx)
        if prop not in state.animated_props:
            return
        ev = Evaluation(widget, ctx, state, EvaluationCause.DELAY_FIRE)
        if self.apply_prop(ev, prop):
            self._engine.writer.flush_now(widget, ctx)

    def _collect_state(self, widget: QWidget, ctx: WidgetState) -> ResolvedRuleState:
        """Collect cascade state and publish target box props for geometry helpers."""
        state = self.collect_rule_state(widget, ctx)
        ctx.style_box_props = dict(state.target_props)
        return state

    def _apply_animated_props(self, ev: Evaluation) -> bool:
        """Drive every animated prop; report whether a style flush is needed."""
        needs_style_update = False
        for prop in ev.state.animated_props:
            if self.apply_prop(ev, prop):
                needs_style_update = True
        return needs_style_update

    # ------------------------------------------------------------------
    # Cascade
    # ------------------------------------------------------------------

    def can_skip_initial_evaluation(self, widget: QWidget, ctx: WidgetState, cause: EvaluationCause) -> bool:
        """Skip base-state Polish work when no rule needs engine-managed behavior."""
        if not cause.snaps_transitions or ctx.css_anim_props or ctx.active_animations:
            return False
        return not any(
            rule.transitions or rule.has_effect_props or rule.has_cursor_prop or rule.has_border_radius_props
            for rule in self._engine.matcher.matching_rules(widget)
        )

    def collect_rule_state(self, widget: QWidget, ctx: WidgetState) -> ResolvedRuleState:
        return self._engine.cascade.collect(widget, ctx)

    # ------------------------------------------------------------------
    # Per-property decision: resolve -> snap/animate
    # ------------------------------------------------------------------

    def apply_prop(self, ev: Evaluation, prop: str) -> bool:
        """Drive one property through resolve -> snap/animate. Returns True if a style flush is needed."""
        self._engine.delays.cancel(ev.ctx, prop)
        if self._is_class_anim_blocked(ev, prop):
            return False
        if self._is_post_clean_noop(ev, prop):
            return False
        resolved = self.resolve_property(ev, prop)
        if resolved is None:
            return False
        if resolved.spec is not None and self._has_uninterpolable_color(prop, resolved.current, resolved.target):
            return self._snap_uninterpolable_color(ev.ctx, prop, resolved.animation, resolved.target)
        if self._is_natural_noop(ev.ctx, prop, resolved):
            return False
        if resolved.spec is None:
            return self._snap_untransitioned(ev, prop, resolved)
        if self._should_snap(ev, prop, resolved.spec):
            return self._snap_to_target(ev, prop, resolved)
        return self._start_or_retarget(ev, prop, resolved)

    @staticmethod
    def _is_class_anim_blocked(ev: Evaluation, prop: str) -> bool:
        """A class-driven animation owns prop until it finishes; other causes must not steal it."""
        return not ev.cause.is_class_driven and prop in ev.ctx.class_anim_props

    @staticmethod
    def _is_post_clean_noop(ev: Evaluation, prop: str) -> bool:
        """Post-clean finish needs no resolve and no natural-size measurement."""
        if prop not in SIZE_PROPS or prop in ev.ctx.css_anim_props or prop not in ev.ctx.active_animations:
            return False
        target_raw = ev.state.target_props.get(prop) or ev.state.base_props.get(prop)
        return not target_raw or target_raw == "auto"

    def resolve_property(self, ev: Evaluation, prop: str) -> ResolvedProperty | None:
        """Resolve current/target/animation/spec for one property."""
        animation = ev.ctx.active_animations.get(prop)
        natural_hint = self._natural_hint(ev, prop, animation)
        base_props = ev.state.base_props
        base_raw = base_props.get(prop, "auto")
        current = self.resolve_current_raw(ev.widget, ev.ctx, prop, base_props, base_raw)
        target, is_natural_target = self.resolve_target_raw(
            ev.widget, base_props, ev.state.target_props, prop, natural_hint, current
        )
        if not target:
            return None
        if prop not in SIZE_PROPS and prop not in ev.ctx.css_anim_props and base_props.get(prop) in (None, "", "auto"):
            # No defined base value: start from target so we snap instead of animating from garbage.
            current = target
        return ResolvedProperty(
            animation=animation,
            current=current,
            target=target,
            is_natural_target=is_natural_target,
            spec=ev.state.transitions.get(prop),
        )

    def resolve_current_raw(
        self, widget: QWidget, ctx: WidgetState, prop: str, base_props: dict[str, str], base_raw: str
    ) -> str:
        """Resolve the CSS value to use as the animation start point."""
        if (inline := ctx.css_anim_props.get(prop)) is not None:
            return inline
        if prop not in SIZE_PROPS:
            return base_raw
        actual = content_box_px(widget, base_props, prop, self._raw_size_px(widget, ctx, prop))
        return f"{actual}px" if actual > 0 else base_raw

    @staticmethod
    def _raw_size_px(widget: QWidget, ctx: WidgetState, prop: str) -> int:
        """Pre-polish snapshot wins during class changes; otherwise read the live widget size."""
        pre_polish_size = ctx.pre_polish_size
        if "width" in prop:
            return pre_polish_size[0] if pre_polish_size is not None else widget.width()
        return pre_polish_size[1] if pre_polish_size is not None else widget.height()

    def resolve_target_raw(
        self,
        widget: QWidget,
        base_props: dict[str, str],
        target_props: dict[str, str],
        prop: str,
        natural_hint: str | None = None,
        current_raw: str | None = None,
    ) -> tuple[str, bool]:
        """Resolve the CSS target value and whether it's a natural (unconstrained) target."""
        target_raw = target_props.get(prop) or base_props.get(prop)
        is_natural_target = prop in SIZE_PROPS and (not target_raw or target_raw == "auto")
        if target_raw == "auto":
            target_raw = natural_hint or self.get_natural_size(widget, base_props, prop, current_raw)
        if not target_raw:
            if prop in SIZE_PROPS:
                target_raw = natural_hint or self.get_natural_size(widget, base_props, prop, current_raw)
            elif "color" in prop:
                target_raw = "white" if prop == "color" else "transparent"
        return target_raw or "", is_natural_target

    def _natural_hint(self, ev: Evaluation, prop: str, anim_obj: Animation | None) -> str | None:
        """Reuse a running size animation's stored natural value so 'auto' doesn't re-measure mid-flight."""
        if not (
            isinstance(anim_obj, GenericPropertyAnimation)
            and prop in ev.ctx.css_anim_props
            and prop in SIZE_PROPS
            and not (ev.state.target_props.get(prop) or ev.state.base_props.get(prop))
            and not ev.cause.is_class_driven
        ):
            return None
        return f"{anim_obj.natural_val:.3f}{anim_obj.unit}"

    @staticmethod
    def _is_natural_noop(ctx: WidgetState, prop: str, resolved: ResolvedProperty) -> bool:
        """Qt already lays out natural sizes itself; without an inline constraint there is nothing to do."""
        if not resolved.is_natural_target or prop in ctx.css_anim_props:
            return False
        return resolved.animation is not None or not ctx.pre_polish_size

    def _snap_untransitioned(self, ev: Evaluation, prop: str, resolved: ResolvedProperty) -> bool:
        """Snap a property with no transition unless Qt already renders the desired value."""
        if (
            prop not in EFFECT_PROPS
            and resolved.animation is None
            and prop not in ev.ctx.css_anim_props
            and resolved.target == ev.state.base_props.get(prop)
            and not self._engine.cascade.needs_qt_border_radius_clamp(ev.widget, ev.state.target_props, prop)
        ):
            return False
        return self._snap_to_target(ev, prop, resolved)

    def _should_snap(self, ev: Evaluation, prop: str, trans: TransitionSpec) -> bool:
        """Return whether transition policy requires an immediate target update."""
        if trans.duration_ms == 0 or not self._engine.animations_enabled or ev.cause.snaps_transitions:
            return True
        return (
            ev.cause is EvaluationCause.RULE_RELOAD
            and prop in BORDER_RADIUS_PROPS
            and self._engine.cascade.needs_qt_border_radius_clamp(ev.widget, ev.state.target_props, prop)
        )

    # ------------------------------------------------------------------
    # Animation run
    # ------------------------------------------------------------------

    def _start_or_retarget(self, ev: Evaluation, prop: str, resolved: ResolvedProperty) -> bool:
        """Create or retarget an animation; report whether a delayed hold value needs a style flush."""
        assert resolved.spec is not None
        trans = resolved.spec
        anim_obj = resolved.animation
        is_running = anim_obj is not None and anim_obj.anim.state() == QAbstractAnimation.State.Running
        if not is_running and trans.delay_ms > 0 and ev.cause is not EvaluationCause.DELAY_FIRE:
            # Hold the start value inline while the delay timer is pending.
            if prop not in EFFECT_PROPS:
                ev.ctx.css_anim_props[prop] = resolved.current
            self._schedule_delay(ev.widget, ev.ctx, prop, trans.delay_ms)
            return prop not in EFFECT_PROPS
        anim_obj = self._prepare_animation(ev, prop, resolved, resolve_easing_curve(trans.easing))
        if anim_obj is None:
            return False
        self._set_target(ev.widget, prop, anim_obj, resolved, ev.state.target_props)
        if not is_running and trans.delay_ms < 0 and anim_obj.anim.state() == QAbstractAnimation.State.Running:
            anim_obj.anim.setCurrentTime(min(-trans.delay_ms, trans.duration_ms))
        self._wire_callbacks(ev, prop, anim_obj)
        return False

    def _prepare_animation(
        self, ev: Evaluation, prop: str, resolved: ResolvedProperty, curve: QEasingCurve
    ) -> Animation | None:
        """Reuse the existing animator's timing or create one seeded with the current value."""
        assert resolved.spec is not None
        if resolved.animation is not None:
            resolved.animation.update_spec(resolved.spec.duration_ms, curve)
            return resolved.animation
        created = self._create_animation(
            ev.widget, prop, resolved.current, resolved.spec.duration_ms, curve, ev.state.base_props
        )
        if created is not None:
            ev.ctx.active_animations[prop] = created
        return created

    @staticmethod
    def _set_target(
        widget: QWidget,
        prop: str,
        animation: Animation,
        resolved: ResolvedProperty,
        target_props: dict[str, str],
    ) -> None:
        """Configure the target value and box-model inputs for an animation."""
        if not isinstance(animation, GenericPropertyAnimation):
            animation.set_target(resolved.target)
            return
        animation.update_box_props(target_props)
        box_size = target_border_radius_box_size(widget, target_props) if prop in BORDER_RADIUS_PROPS else None
        animation.set_target(resolved.target, clean_on_finish=resolved.is_natural_target, box_size=box_size)

    def _wire_callbacks(self, ev: Evaluation, prop: str, animation: Animation) -> None:
        """Attach the completion behavior required by the current evaluation cause."""
        if animation.anim.state() != QAbstractAnimation.State.Running:
            return
        if ev.cause.is_class_driven:
            self._wire_class_callback(ev.widget, ev.ctx, prop, animation)
        if ev.cause.is_clicked_driven and prop in ev.ctx.clicked_anim_props:
            self._wire_clicked_callback(ev.widget, ev.ctx, prop, animation)

    def _wire_class_callback(self, widget: QWidget, ctx: WidgetState, prop: str, anim_obj: Animation) -> None:
        """Track class-driven anims so their finish re-evaluates any queued class state."""
        ctx.class_anim_props.add(prop)
        gen = ctx.class_anim_gen
        wid = id(widget)

        def _on_done(_w: QWidget = widget, _p: str = prop, _wid: int = wid, _gen: int = gen) -> None:
            c = self._engine.store.contexts.get(_wid)
            if c and _gen == c.class_anim_gen and _p in c.class_anim_props:
                c.class_anim_props.discard(_p)
                self.evaluate(_w, cause=EvaluationCause.CLASS_ANIMATION_FINISH)

        self._replace_finished_callback(anim_obj, prop, ctx.class_anim_callbacks, _on_done)

    def _wire_clicked_callback(self, widget: QWidget, ctx: WidgetState, prop: str, anim_obj: Animation) -> None:
        """Track :clicked anims so the pseudo clears once the last one finishes."""
        gen = ctx.clicked_anim_gen
        wid = id(widget)

        def _on_done(_w: QWidget = widget, _p: str = prop, _wid: int = wid, _gen: int = gen) -> None:
            c = self._engine.store.contexts.get(_wid)
            if c and _gen == c.clicked_anim_gen and _p in c.clicked_anim_props:
                c.clicked_anim_props.discard(_p)
                if not c.clicked_anim_props:
                    self._engine.deactivate_clicked(_w, _wid, _gen)

        self._replace_finished_callback(anim_obj, prop, ctx.clicked_anim_callbacks, _on_done)

    @staticmethod
    def _replace_finished_callback(
        anim_obj: Animation,
        prop: str,
        callbacks: dict[str, Callable[[], None]],
        callback: Callable[[], None],
    ) -> None:
        """Replace a per-property finished callback without accumulating signal connections."""
        if (old_callback := callbacks.pop(prop, None)) is not None:
            safe_disconnect(anim_obj.anim.finished, old_callback)
        callbacks[prop] = callback
        anim_obj.anim.finished.connect(callback)

    # ------------------------------------------------------------------
    # Orphaned animation cleanup
    # ------------------------------------------------------------------

    def cleanup_orphans(self, ctx: WidgetState, state: ResolvedRuleState) -> bool:
        """Snap/stop animations for props no longer covered by any rule."""
        for prop in list(ctx.pending_delays):
            if prop not in state.animated_props:
                self._engine.delays.cancel(ctx, prop)
        needs_update = False
        for prop, orphan in list(ctx.active_animations.items()):
            if prop in state.animated_props:
                continue
            if self._remove_orphan(ctx, state, prop, orphan):
                needs_update = True
        if self._evict_stale(ctx, state):
            needs_update = True
        return needs_update

    def _remove_orphan(self, ctx: WidgetState, state: ResolvedRuleState, prop: str, orphan: Animation) -> bool:
        """Settle and release one animation whose property is no longer engine-managed."""
        ctx.class_anim_props.discard(prop)
        if (old_callback := ctx.class_anim_callbacks.pop(prop, None)) is not None:
            safe_disconnect(orphan.anim.finished, old_callback)
        snap_target, is_natural_snap = self._orphan_snap_target(orphan, state, prop)
        if snap_target:
            self._snap_orphan(ctx, prop, orphan, snap_target, is_natural_snap)
        else:
            self._stop_orphan_effect(orphan)
        del ctx.active_animations[prop]
        orphan.deleteLater()
        return self._needs_style_flush(orphan)

    @staticmethod
    def _needs_style_flush(anim_obj: Animation) -> bool:
        """Effect animators render via QGraphicsEffect, not stylesheets — no flush needed."""
        return not isinstance(anim_obj, (OpacityAnimation, BoxShadowHandle))

    @staticmethod
    def _orphan_snap_target(orphan: Animation, state: ResolvedRuleState, prop: str) -> tuple[str | None, bool]:
        """Find the base value an orphan should settle on, including natural size fallback."""
        snap_target = state.base_props.get(prop)
        if snap_target == "auto":
            snap_target = None
        is_natural_snap = not snap_target and prop in SIZE_PROPS
        if is_natural_snap:
            snap_target = get_preferred_size_fallback(orphan.widget, state.base_props, prop)
        return snap_target, is_natural_snap

    def _snap_orphan(
        self, ctx: WidgetState, prop: str, orphan: Animation, snap_target: str, is_natural_snap: bool
    ) -> None:
        """Move an orphaned animation to its final base or natural value."""
        if isinstance(orphan, ColorAnimation) and not self._is_interpolable_color(snap_target):
            orphan.anim.stop()
            ctx.css_anim_props.pop(prop, None)
        elif is_natural_snap and isinstance(orphan, GenericPropertyAnimation):
            orphan.snap_to_natural()
        else:
            orphan.snap_to(snap_target)

    def _stop_orphan_effect(self, orphan: Animation) -> None:
        """Stop an orphan with no base value and clear any engine-owned graphics effect."""
        orphan.anim.stop()
        if isinstance(orphan, BoxShadowHandle):
            apply_shadow_to_widget(orphan.widget, None, self._engine.effect_priority)
        elif isinstance(orphan, OpacityAnimation):
            try:
                orphan.widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

    @staticmethod
    def _evict_stale(ctx: WidgetState, state: ResolvedRuleState) -> bool:
        """Remove inline values that have neither a matching rule nor a live animation."""
        stale_props = {
            prop
            for prop in ctx.css_anim_props
            if prop not in state.animated_props and prop not in ctx.active_animations and prop not in state.base_props
        }
        for prop in stale_props:
            del ctx.css_anim_props[prop]
        return bool(stale_props)

    # ------------------------------------------------------------------
    # Value helpers: natural size, color, delay, snap, clamp
    # ------------------------------------------------------------------

    def get_natural_size(
        self, widget: QWidget, base_props: dict[str, str], prop: str, current_raw: str | None = None
    ) -> str:
        return get_natural_size(widget, self._engine.get_context(widget), base_props, prop, current_raw)

    @staticmethod
    def _is_color_prop(prop: str) -> bool:
        """QSS color properties handled by ColorAnimation."""
        return prop == "color" or prop.endswith("-color")

    @staticmethod
    def _is_interpolable_color(value: str) -> bool:
        """Solid colors ColorAnimation can interpolate (excludes gradients)."""
        return parse_color(value).isValid()

    def _has_uninterpolable_color(self, prop: str, current_raw: str, target_raw: str) -> bool:
        """A color transition with a static-only endpoint such as a gradient must snap."""
        if not self._is_color_prop(prop):
            return False
        return not self._is_interpolable_color(current_raw) or not self._is_interpolable_color(target_raw)

    def _snap_uninterpolable_color(
        self, ctx: WidgetState, prop: str, anim_obj: Animation | None, target_raw: str
    ) -> bool:
        """Snap a color prop when either endpoint is not a solid color."""
        if isinstance(anim_obj, ColorAnimation):
            anim_obj.anim.stop()
        if self._is_interpolable_color(target_raw):
            if isinstance(anim_obj, ColorAnimation):
                anim_obj.snap_to(target_raw)
            else:
                ctx.css_anim_props[prop] = target_raw
            return True
        if prop in ctx.css_anim_props:
            del ctx.css_anim_props[prop]
            return True
        return False

    def _schedule_delay(self, widget: QWidget, ctx: WidgetState, prop: str, delay_ms: int) -> None:
        """Schedule prop's animation to start after delay_ms."""
        wid = id(widget)

        def _fire(_w: QWidget = widget, _p: str = prop, _wid: int = wid) -> None:
            c = self._engine.store.contexts.get(_wid)
            if c is not None:
                c.pending_delays.pop(_p, None)
            else:
                try:
                    self._engine.delays.cancel(ctx, _p)
                except RuntimeError:
                    pass
            try:
                self.fire_delayed_prop(_w, _p)
            except RuntimeError:
                pass

        self._engine.delays.schedule(ctx, prop, delay_ms, _fire)

    def _snap_to_target(self, ev: Evaluation, prop: str, resolved: ResolvedProperty) -> bool:
        """Write the target value instantly. Returns True if a style flush is needed."""
        box_props = ev.state.target_props
        anim_obj = resolved.animation
        if anim_obj is not None:
            self._snap_existing(ev.widget, prop, anim_obj, resolved.target, resolved.is_natural_target, box_props)
            return self._needs_style_flush(anim_obj)
        if prop in EFFECT_PROPS:
            new_anim = self._create_animation(ev.widget, prop, resolved.target, 0, QEasingCurve.Type.Linear, box_props)
            if new_anim is not None:
                ev.ctx.active_animations[prop] = new_anim
            return False
        if resolved.is_natural_target:
            ev.ctx.css_anim_props.pop(prop, None)
            return True
        ev.ctx.css_anim_props[prop] = self._clamp_target(ev.widget, prop, resolved.target, box_props)
        return True

    @staticmethod
    def _snap_existing(
        widget: QWidget,
        prop: str,
        animation: Animation,
        target_raw: str,
        is_natural_target: bool,
        target_props: dict[str, str],
    ) -> None:
        """Move an existing animation directly to its target."""
        if not isinstance(animation, GenericPropertyAnimation):
            animation.snap_to(target_raw)
            return
        if is_natural_target:
            animation.snap_to_natural()
            return
        animation.update_box_props(target_props)
        box_size = target_border_radius_box_size(widget, target_props) if prop in BORDER_RADIUS_PROPS else None
        animation.snap_to(target_raw, box_size)

    @staticmethod
    def _clamp_target(widget: QWidget, prop: str, target_raw: str, target_props: dict[str, str]) -> str:
        """Clamp a static border-radius value to what fits the target box geometry."""
        if prop not in BORDER_RADIUS_PROPS:
            return target_raw
        parsed = parse_css_numeric(target_raw)
        if parsed is None:
            return target_raw
        value, unit = parsed
        if unit != "px":
            return target_raw
        box_size = target_border_radius_box_size(widget, target_props)
        clamped = clamp_border_radius(widget, prop, max(0.0, value), unit, target_props, box_size)
        return f"{clamped:.3f}{unit}"

    def _create_animation(
        self,
        widget: QWidget,
        prop: str,
        initial_raw: str,
        duration_ms: int,
        curve: QEasingCurve | QEasingCurve.Type,
        box_props: dict[str, str] | None = None,
    ) -> Animation | None:
        """Instantiate the correct Animation subclass for a CSS property."""
        ctx = self._engine.get_context(widget)
        return create_animator(
            widget,
            prop,
            initial_raw,
            duration_ms,
            curve,
            ctx=ctx,
            box_props=box_props,
            style_flush_callback=lambda w, c: self._engine.writer.schedule(w, c),
            effect_priority=self._engine.effect_priority,
            parent=self._engine,
        )
