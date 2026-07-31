import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from .constants import (
    BORDER_RADIUS_PROPS,
    CURSOR_MAP,
    EFFECT_PROPS,
    ENGINE_EVENT_TYPES,
    NON_NEGATIVE_PROPS,
    PSEUDO_EVENTS,
    SIZE_PROPS,
    SUPPORTED_NUMERIC_PROPS,
)
from .easing import resolve_easing_curve
from .handlers import (
    BoxShadowHandle,
    ColorAnimation,
    GenericPropertyAnimation,
    OpacityAnimation,
    clamp_border_radius,
    target_border_radius_box_size,
)
from .layout_measure import content_box_px, get_natural_size, get_preferred_size_fallback
from .matcher import RuleMatcher
from .qt_compat.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, Qt, QTimer
from .qt_compat.QtGui import QMouseEvent
from .qt_compat.QtWidgets import QAbstractButton, QApplication, QWidget
from .types import Animation, EvaluationCause, InternalWriteReason, ResolvedProperty, ResolvedRuleState, WidgetContext
from .utils import (
    apply_shadow_to_widget,
    parse_color,
    parse_css_numeric,
    parse_css_val,
    safe_disconnect,
    scoped_anim_style,
    update_shadow_ancestor,
)

if TYPE_CHECKING:
    from qt_css_engine.css_parser import StyleRule, TransitionSpec


event_logger = logging.getLogger("qt_css_engine.event")


class TransitionEngine(QObject):
    """
    Core CSS transition engine for PyQt6/PySide6.

    Installed as a global event filter on QApplication. Intercepts hover, mouse,
    and focus events to track widget pseudo-states, evaluates the CSS cascade,
    and drives smooth property animations via Qt's animation framework.

    Event handlers only update widget context and trigger evaluation. Evaluation then
    follows one path: collect rule state, resolve each property, animate or snap it,
    clean up orphaned animations, and flush the resulting inline style once.
    """

    pseudo_priority: dict[str, int] = {
        "": 0,
        ":hover": 1,
        ":focus": 1,
        ":pressed": 2,
        ":checked": 1,
        ":clicked": 3,
        ":active": 1,
    }

    # Which effect wins the widget's single graphics-effect slot when both opacity and
    # box-shadow are declared on the same widget. The loser becomes a silent no-op.
    # "box-shadow" → QGraphicsDropShadowEffect takes priority over opacity.
    # "opacity"    → QGraphicsOpacityEffect takes priority over box-shadow.
    effect_priority: str = "opacity"

    def __init__(self, rules: list[StyleRule], parent: QObject | None = None, startup_delay_ms: int = 100) -> None:
        """
        Initialise the engine with a parsed rule set.

        startup_delay_ms: animations are suppressed for this many milliseconds after
        construction so that initial layout polish events don't trigger spurious transitions.
        Set to 0 to enable immediately (synchronous — useful in tests).
        """
        super().__init__(parent)
        self._matcher = RuleMatcher(rules)

        if startup_delay_ms <= 0:
            self.animations_enabled = True
        else:
            self.animations_enabled = False
            QTimer.singleShot(startup_delay_ms, lambda: self._on_startup_done())

        # One source of truth for all per-widget state.
        self._contexts: dict[int, WidgetContext] = {}
        # Widgets that have at least one :active rule — populated at Polish time for O(1) activate/deactivate.
        self._active_rule_widgets: dict[int, QWidget] = {}
        # Checkable widget IDs already connected to toggled signal.
        self._connected_checkable_ids: set[int] = set()

        # Enable event logging if the CSS_ENGINE_EVENT_LOGGING env var is set.
        if os.environ.get("CSS_ENGINE_EVENT_LOGGING", "").lower() not in ("1", "true", "yes"):
            event_logger.disabled = True
        # When True, middle/right clicks are ignored entirely (no :pressed/:clicked animations).
        # Controlled by CSS_ENGINE_LEFT_CLICK_ONLY env var.
        self._left_click_only: bool = os.environ.get("CSS_ENGINE_LEFT_CLICK_ONLY", "").lower() in ("1", "true", "yes")
        # Timestamp of the last non-left mouse press event claimed by a widget with matching rules.
        # Prevents :pressed from propagating to ancestor widgets on middle/right click.
        self._claimed_mouse_event_ts: int = -1

        # Deferred Polish burst state: widgets queued for evaluation after the burst drains.
        self._polish_pending: bool = False
        self._polish_queue: list[QWidget] = []
        self._polish_force_ids: set[int] = set()

    # -------------------------------------------------------------------------
    # Core internal match helpers
    # -------------------------------------------------------------------------

    def _should_evaluate(self, widget: QWidget) -> bool:
        return self._matcher.should_evaluate(widget, self._contexts.get(id(widget)))

    # -------------------------------------------------------------------------
    # Core logic & delegation
    # -------------------------------------------------------------------------

    def _on_startup_done(self) -> None:
        """Enable animations after the startup delay has elapsed."""
        self.animations_enabled = True

    def _ctx(self, widget: QWidget) -> WidgetContext:
        """Get or create the context for a widget."""
        wid = id(widget)
        ctx = self._contexts.get(wid)
        if ctx is None:
            ctx = WidgetContext()
            self._contexts[wid] = ctx
            widget.destroyed.connect(lambda: self._on_widget_destroyed(widget))
        return ctx

    # -------------------------------------------------------------------------
    # Event filtering and pseudo-state tracking
    # -------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore
        """Intercept widget events to track pseudo-states and trigger CSS transitions."""
        event_type = event.type()
        if event_type not in ENGINE_EVENT_TYPES or not isinstance(watched, QWidget):
            return False
        self._dispatch_widget_event(watched, event, event_type)
        return False

    def _dispatch_widget_event(self, widget: QWidget, event: QEvent, event_type: QEvent.Type) -> None:
        """Route a relevant Qt event to the focused handler for that event."""
        if event_type in PSEUDO_EVENTS:
            self._on_pseudo_event(widget, event, event_type)
            return

        match event_type:
            case QEvent.Type.Polish:
                self._on_polish(widget)
            case QEvent.Type.Resize:
                self._on_resize(widget)
            case QEvent.Type.DynamicPropertyChange:
                if self._is_class_property_change(event):
                    self._on_class_change(widget)
            case QEvent.Type.ParentChange:
                self._on_parent_change(widget)
            case QEvent.Type.WindowActivate:
                self._on_window_activate(widget)
            case QEvent.Type.WindowDeactivate:
                self._on_window_deactivate(widget)
            case QEvent.Type.Leave if widget.isWindow():
                # Qt may omit HoverLeave when focus moves to a popup window.
                self._on_window_deactivate(widget, clear_active=False)
            case _:
                pass

    @staticmethod
    def _is_class_property_change(event: QEvent) -> bool:
        """Return whether a DynamicPropertyChange event targets the CSS class property."""
        property_name = getattr(event, "propertyName", lambda: None)()
        return property_name is not None and getattr(property_name, "data", lambda: b"")() == b"class"

    def _on_pseudo_event(self, widget: QWidget, event: QEvent, event_type: QEvent.Type) -> None:
        """Update one widget's pseudo-state and evaluate any resulting transition."""
        is_mouse_press = event_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
        if is_mouse_press and isinstance(event, QMouseEvent) and event.button() != Qt.MouseButton.LeftButton:
            if self._left_click_only:
                return
            timestamp = event.timestamp()
            if timestamp == self._claimed_mouse_event_ts or not self._should_evaluate(widget):
                return
            self._claimed_mouse_event_ts = timestamp

        ctx = self._ctx(widget)
        updated = self._update_pseudos(ctx.active_pseudos, event_type)
        cause = self._prepare_clicked(widget, ctx, updated) if is_mouse_press else EvaluationCause.PSEUDO_STATE
        if updated == ctx.active_pseudos:
            return
        ctx.active_pseudos = updated
        self._evaluate_widget_state(widget, cause=cause)
        if cause is EvaluationCause.CLICKED_ACTIVATION:
            self._finish_clicked_activation(widget, ctx)

    def _on_polish(self, widget: QWidget) -> None:
        """Handle Polish events — evaluate initial widget state on first polish."""
        ctx = self._contexts.get(id(widget))
        # Ignore Polish events triggered by our own internal style writes.
        if ctx is not None and ctx.internal_write_depth > 0:
            return
        # Wire up the toggled signal for checkable widgets (idempotent).
        self._connect_checkable(widget)
        # Skip widgets that no animated/effect rule could ever touch.
        if not self._should_evaluate(widget):
            return
        if ctx is not None and ctx.active_animations:
            return
        # Defer all expensive work to after the burst. Qt fires Polish for every child widget synchronously
        self._queue_polish_evaluation(widget)

    def _on_resize(self, widget: QWidget) -> None:
        """Refresh static border-radius clamps after layout assigns a new widget size."""
        if not self._matcher.has_border_radius_rules:
            return
        ctx = self._contexts.get(id(widget))
        if ctx is not None and ctx.internal_write_depth > 0:
            return
        if ctx is not None and self._has_running_animation(ctx):
            return
        if not self._should_evaluate(widget):
            return
        if not any(p in BORDER_RADIUS_PROPS for rule in self._matcher.matching_rules(widget) for p in rule.properties):
            return
        event_logger.debug("On resize event: %s", widget)
        self._queue_polish_evaluation(widget, force=True)

    @staticmethod
    def _has_running_animation(ctx: WidgetContext) -> bool:
        """Return True while any registered animation is actively transitioning."""
        return any(
            anim_obj.anim.state() == QAbstractAnimation.State.Running for anim_obj in ctx.active_animations.values()
        )

    def _queue_polish_evaluation(self, widget: QWidget, *, force: bool = False) -> None:
        """Queue a widget for a deferred polish-style state evaluation."""
        if force:
            self._polish_force_ids.add(id(widget))
        if not self._polish_pending:
            self._polish_pending = True
            self._polish_queue.clear()
            QTimer.singleShot(0, self._flush_polish_queue)
        self._polish_queue.append(widget)

    def _flush_polish_queue(self) -> None:
        """Drain the deferred Polish evaluation queue after a burst completes."""
        self._polish_pending = False
        widgets, self._polish_queue = self._polish_queue, []
        force_ids, self._polish_force_ids = self._polish_force_ids, set()
        seen: set[int] = set()
        for w in widgets:
            try:
                wid = id(w)
                if wid in seen:
                    continue
                seen.add(wid)
                self._ensure_wa_hover(w)
                self._seed_active_pseudo(w)
                ctx = self._contexts.get(id(w))
                if wid in force_ids or ctx is None or not ctx.active_animations:
                    self._evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass

    def _on_class_change(self, widget: QWidget) -> None:
        """Handle class property change — snapshot size, unpolish/polish, and kick off animations."""
        # Invalidate per-widget rule cache.
        self._matcher.clear_caches()
        # Skip widgets that no animated rule could touch.
        if not self._should_evaluate(widget):
            return
        event_logger.debug("On class change: %s", widget)
        ctx = self._ctx(widget)
        # Snapshot actual size before Qt's polish snaps it to the new stylesheet values.
        ctx.pre_polish_size = (widget.width(), widget.height())
        # Guard the synchronous Polish so it doesn't snap animated props before we animate them.
        ctx.internal_write_depth += 1
        ctx.internal_write_reason = InternalWriteReason.CLASS_CHANGE
        try:
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
        finally:
            ctx.internal_write_depth -= 1
            if ctx.internal_write_depth == 0:
                ctx.internal_write_reason = None
        widget.update()
        update_shadow_ancestor(widget)
        # Fresh generation — stale finished callbacks from prior class changes become no-ops.
        ctx.class_anim_gen += 1
        ctx.class_anim_props.clear()
        self._evaluate_widget_state(widget, cause=EvaluationCause.CLASS_CHANGE)
        ctx.pre_polish_size = None

    def _on_parent_change(self, widget: QWidget) -> None:
        """Handle reparenting; ancestor-dependent selectors may now match differently."""
        self._matcher.clear_caches()
        for w in (widget, *widget.findChildren(QWidget)):
            try:
                if self._should_evaluate(w):
                    self._queue_polish_evaluation(w, force=True)
            except RuntimeError:
                pass

    def _on_window_activate(self, widget: QWidget) -> None:
        """Set :active on children that have :active rules when the window gains focus."""
        for child in self._active_rule_widgets.values():
            try:
                if child.window() is not widget:
                    continue
            except RuntimeError:
                continue
            ctx = self._ctx(child)
            if ":active" not in ctx.active_pseudos:
                event_logger.debug("On window activate: %s", widget)
                ctx.active_pseudos.add(":active")
                self._evaluate_widget_state(child, cause=EvaluationCause.PSEUDO_STATE)

    def _on_window_deactivate(self, widget: QWidget, *, clear_active: bool = True) -> None:
        """Clear stuck :hover/:pressed/:active states when the window loses focus."""
        # Qt may not deliver HoverLeave when a child dialog steals focus.
        _TRANSIENT_PSEUDOS = {":hover", ":pressed", ":active"} if clear_active else {":hover", ":pressed"}
        for child in widget.findChildren(QWidget):
            ctx = self._contexts.get(id(child))
            if ctx is None:
                continue
            stuck = ctx.active_pseudos & _TRANSIENT_PSEUDOS
            if stuck:
                event_logger.debug("Clearing stuck pseudos: %s", child)
                ctx.active_pseudos -= stuck
                self._evaluate_widget_state(child, cause=EvaluationCause.WINDOW_DEACTIVATE)

    def _prepare_clicked(self, widget: QWidget, ctx: WidgetContext, updated: set[str]) -> EvaluationCause:
        """
        If the widget has :clicked rules and :clicked is not already active, add :clicked to
        *updated* and initialise clicked tracking on *ctx*.  Returns the EvaluationCause to use.
        """
        if ":clicked" in ctx.active_pseudos:
            return EvaluationCause.PSEUDO_STATE  # Forward animation already running; ignore re-click.
        if not any(":clicked" in rule.pseudo_set for rule in self._matcher.matching_rules(widget)):
            return EvaluationCause.PSEUDO_STATE
        updated.add(":clicked")
        ctx.clicked_anim_gen += 1
        ctx.clicked_anim_props.clear()
        for rule in self._matcher.matching_rules(widget):
            if ":clicked" in rule.pseudo_set:
                ctx.clicked_anim_props.update(rule.properties.keys())
        return EvaluationCause.CLICKED_ACTIVATION

    def _finish_clicked_activation(self, widget: QWidget, ctx: WidgetContext) -> None:
        """
        Called after CLICKED_ACTIVATION evaluation.  Prune clicked_anim_props to only
        properties with a running animation; if none remain (all snapped), schedule an
        immediate deactivation so the reverse animation fires in the next event-loop tick.
        """
        ctx.clicked_anim_props = {
            p
            for p in ctx.clicked_anim_props
            if p in ctx.active_animations and ctx.active_animations[p].anim.state() == QAbstractAnimation.State.Running
        }
        if not ctx.clicked_anim_props:
            wid = id(widget)
            gen = ctx.clicked_anim_gen
            QTimer.singleShot(0, lambda: self._deactivate_clicked(widget, wid, gen))

    def _deactivate_clicked(self, widget: QWidget, wid: int, gen: int) -> None:
        """Remove :clicked from active_pseudos and re-evaluate to trigger the reverse animation."""
        ctx = self._contexts.get(wid)
        if ctx is None or gen != ctx.clicked_anim_gen or ":clicked" not in ctx.active_pseudos:
            return
        ctx.active_pseudos.discard(":clicked")
        try:
            self._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)
        except RuntimeError:
            pass

    def _ensure_wa_hover(self, widget: QWidget) -> None:
        """Set WA_Hover on widget if it matches any rule with a :hover pseudo-class."""
        if widget.testAttribute(Qt.WidgetAttribute.WA_Hover):
            return  # already set
        if not self._should_evaluate(widget):
            return
        if any(":hover" in rule.pseudo_set for rule in self._matcher.matching_rules(widget)):
            widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _seed_active_pseudo(self, widget: QWidget) -> None:
        """Add :active to the widget's pseudo set at Polish time if its window is currently active."""
        if not any(":active" in r.pseudo_set for r in self._matcher.matching_rules(widget)):
            return
        self._active_rule_widgets[id(widget)] = widget
        if not widget.isActiveWindow():
            return
        if not self._should_evaluate(widget):
            return
        self._ctx(widget).active_pseudos.add(":active")

    def _connect_checkable(self, widget: QWidget) -> None:
        """Connect to toggled signal for checkable buttons and sync initial :checked state."""
        if not isinstance(widget, QAbstractButton):
            return
        wid = id(widget)
        if wid in self._connected_checkable_ids:
            return
        self._connected_checkable_ids.add(wid)
        if widget.isChecked():
            self._ctx(widget).active_pseudos.add(":checked")

        def _on_toggle(checked: bool, w: QWidget = widget) -> None:
            self._on_checked_changed(w, checked)

        widget.toggled.connect(_on_toggle)

    def _on_checked_changed(self, widget: QWidget, checked: bool) -> None:
        """Sync :checked pseudo-state and re-evaluate transitions on button toggle."""
        ctx = self._ctx(widget)
        if checked:
            ctx.active_pseudos.add(":checked")
        else:
            ctx.active_pseudos.discard(":checked")
        self._evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    def _update_pseudos(self, pseudos: set[str], event_type: QEvent.Type) -> set[str]:
        """Return an updated pseudo-state set reflecting the given Qt event."""
        updated = pseudos.copy()
        if event_type == QEvent.Type.HoverEnter:
            updated.add(":hover")
        elif event_type == QEvent.Type.HoverLeave:
            updated.discard(":hover")
        elif event_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            updated.add(":pressed")
        elif event_type == QEvent.Type.MouseButtonRelease:
            updated.discard(":pressed")
        elif event_type == QEvent.Type.FocusIn:
            updated.add(":focus")
        elif event_type == QEvent.Type.FocusOut:
            updated.discard(":focus")
        return updated

    # -------------------------------------------------------------------------
    # Widget lifecycle tracking
    # -------------------------------------------------------------------------

    def _on_widget_destroyed(self, widget: QWidget) -> None:
        """Remove all engine state for a destroyed widget and stop its animations."""
        wid = id(widget)
        self._connected_checkable_ids.discard(wid)
        self._matcher.rule_cache.pop(wid, None)
        self._active_rule_widgets.pop(wid, None)
        ctx = self._contexts.pop(wid, None)
        if ctx is None:
            return
        event_logger.debug("On widget destroyed: %s, %s", widget.__class__, wid)
        self._cancel_all_pending_delays(ctx)
        self._disconnect_finished_callbacks(ctx, ctx.class_anim_callbacks)
        self._disconnect_finished_callbacks(ctx, ctx.clicked_anim_callbacks)
        ctx.class_anim_props.clear()
        ctx.clicked_anim_props.clear()
        self._stop_animations(ctx)

    def _cancel_all_pending_delays(self, ctx: WidgetContext) -> None:
        """Cancel every delayed transition currently held by a widget."""
        for prop in list(ctx.pending_delays):
            self._cancel_pending_delay(ctx, prop)

    @staticmethod
    def _disconnect_finished_callbacks(ctx: WidgetContext, callbacks: dict[str, Callable[[], None]]) -> None:
        """Disconnect callbacks held for animations that are about to be discarded."""
        for prop, callback in callbacks.items():
            anim_obj = ctx.active_animations.get(prop)
            if anim_obj is not None:
                try:
                    safe_disconnect(anim_obj.anim.finished, callback)
                except RuntimeError, TypeError:
                    # During Qt shutdown, child QVariantAnimations may already be gone.
                    pass
        callbacks.clear()

    def _stop_animations(self, ctx: WidgetContext, *, clear_effects: bool = False) -> None:
        """Stop and release all animation objects in a widget context."""
        for anim_obj in ctx.active_animations.values():
            try:
                anim_obj.anim.stop()
                if clear_effects:
                    if isinstance(anim_obj, BoxShadowHandle):
                        apply_shadow_to_widget(anim_obj.widget, None, self.effect_priority)
                    elif isinstance(anim_obj, OpacityAnimation):
                        anim_obj.widget.setGraphicsEffect(None)
                anim_obj.deleteLater()
            except RuntimeError:
                pass
        ctx.active_animations.clear()

    # -------------------------------------------------------------------------
    # State evaluation
    # -------------------------------------------------------------------------

    def _evaluate_widget_state(self, widget: QWidget, cause: EvaluationCause = EvaluationCause.DIRECT) -> None:
        """Evaluate all animated CSS properties for widget and start, update, or snap animations."""
        if not self._should_evaluate(widget):
            return

        ctx = self._ctx(widget)
        if self._can_skip_initial_evaluation(widget, ctx, cause):
            return

        state = self._collect_rule_state(widget, ctx)
        ctx.style_box_props = dict(state.target_props)
        old_immediate = ctx.style_flush_immediate
        ctx.style_flush_immediate = old_immediate or cause.is_class_driven
        try:
            needs_style_update = False
            for prop in state.animated_props:
                if self._apply_prop_animation(widget, ctx, prop, state, cause):
                    needs_style_update = True
            if self._cleanup_orphans(ctx, state):
                needs_style_update = True
            if needs_style_update:
                event_logger.debug("Updating style: %s", widget)
                self._flush_widget_style_now(widget, ctx)
            self._apply_cursor(widget, ctx, state.target_props)
        finally:
            ctx.style_flush_immediate = old_immediate

    def _can_skip_initial_evaluation(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        cause: EvaluationCause,
    ) -> bool:
        """Skip base-state Polish work when no rule needs engine-managed behavior."""
        if not cause.snaps_transitions or ctx.css_anim_props or ctx.active_animations:
            return False

        for rule in self._matcher.matching_rules(widget):
            if rule.transitions or "cursor" in rule.properties:
                return False
            if any(prop in EFFECT_PROPS or prop in BORDER_RADIUS_PROPS for prop in rule.properties):
                return False
        return True

    def _schedule_style_flush(self, widget: QWidget, ctx: WidgetContext) -> None:
        """Queue one stylesheet write for this widget after the current burst of animation ticks."""
        if ctx.style_flush_immediate or ctx.class_anim_props:
            self._flush_widget_style_now(widget, ctx)
            return
        if ctx.style_flush_pending:
            return
        ctx.style_flush_pending = True
        wid = id(widget)
        QTimer.singleShot(0, lambda: self._flush_scheduled_widget_style(widget, wid))

    def _flush_scheduled_widget_style(self, widget: QWidget, wid: int) -> None:
        ctx = self._contexts.get(wid)
        if ctx is None or not ctx.style_flush_pending:
            return
        try:
            self._flush_widget_style_now(widget, ctx)
        except RuntimeError:
            ctx.style_flush_pending = False

    def _flush_widget_style_now(self, widget: QWidget, ctx: WidgetContext) -> None:
        """Normalize interdependent inline props and apply them as one scoped stylesheet."""
        ctx.style_flush_pending = False
        self._normalize_dependent_anim_props(widget, ctx)
        widget.setStyleSheet(scoped_anim_style(widget, ctx.css_anim_props))
        update_shadow_ancestor(widget)

    def _normalize_dependent_anim_props(self, widget: QWidget, ctx: WidgetContext) -> None:
        """
        Clamp radius values against the same pending box model that is about to be applied.
        """
        props = ctx.css_anim_props
        box_props = {**ctx.style_box_props, **props}
        box_size = target_border_radius_box_size(widget, box_props)
        for prop in BORDER_RADIUS_PROPS:
            raw = props.get(prop)
            parsed = parse_css_numeric(raw)
            if parsed is None:
                continue
            value, unit = parsed
            clamped = clamp_border_radius(widget, prop, max(0.0, value), unit, box_props, box_size)
            if clamped != value:
                props[prop] = f"{clamped:.3f}{unit}"

    def _expand_all_transitions(
        self,
        ctx: WidgetContext,
        state: ResolvedRuleState,
    ) -> None:
        """Expand `transition: all` to every animatable property present."""
        all_spec = state.transitions.pop("all", None)
        state.animated_props.discard("all")
        for prop in set(state.base_props) | set(state.target_props):
            if self._is_animatable(prop):
                state.animated_props.add(prop)
                if prop not in state.transitions and all_spec is not None:
                    state.transitions[prop] = all_spec
        # Engine-managed props set by a prior class-change may be absent from current rules.
        if all_spec is not None:
            engine_managed: set[str] = set(ctx.css_anim_props) | set(ctx.active_animations)
            for prop in engine_managed:
                if prop not in state.animated_props and self._is_animatable(prop) and prop not in EFFECT_PROPS:
                    state.animated_props.add(prop)
                    state.transitions[prop] = all_spec

    def _collect_border_radius_props(self, widget: QWidget, ctx: WidgetContext, state: ResolvedRuleState) -> None:
        """Collect static border-radius properties that need clamping."""
        for prop in BORDER_RADIUS_PROPS:
            if prop in state.target_props and (
                self._needs_qt_border_radius_clamp(widget, state.target_props, prop) or prop in ctx.css_anim_props
            ):
                state.animated_props.add(prop)

    def _collect_rule_state(self, widget: QWidget, ctx: WidgetContext) -> ResolvedRuleState:
        """
        Evaluate the CSS cascade for widget.

        The returned state contains the base/target cascade, the transitions selected for the
        active pseudo state, and the properties that need engine handling.
        """
        state = ResolvedRuleState()
        trans_priority: dict[str, int] = {}
        pseudos = ctx.active_pseudos
        for rule in self._matcher.matching_rules(widget):
            rule_in_target = not rule.pseudo_set or rule.pseudo_set <= pseudos
            priority = sum(self.pseudo_priority.get(pseudo, 0) for pseudo in rule.pseudo_set) if rule_in_target else -1
            for trans in rule.transitions:
                state.animated_props.add(trans.prop)
                if rule_in_target and priority >= trans_priority.get(trans.prop, -1):
                    state.transitions[trans.prop] = trans
                    trans_priority[trans.prop] = priority
            if not rule.pseudo_set:
                state.base_props.update(rule.properties)
            if rule_in_target:
                state.target_props.update(rule.properties)

        # Expand `transition: all` to every animatable property present in base/target.
        if "all" in state.animated_props:
            self._expand_all_transitions(ctx, state)

        # Effect props need engine handling even without a transition declaration.
        for prop in EFFECT_PROPS:
            if prop in state.base_props or prop in state.target_props:
                state.animated_props.add(prop)

        # Static border-radius clamping.
        self._collect_border_radius_props(widget, ctx, state)

        return state

    def _needs_qt_border_radius_clamp(self, widget: QWidget, target_props: dict[str, str], prop: str) -> bool:
        raw = target_props.get(prop)
        parsed = parse_css_numeric(raw)
        if parsed is None:
            return False
        value, unit = parsed
        box_size = target_border_radius_box_size(widget, target_props)
        return clamp_border_radius(widget, prop, max(0.0, value), unit, target_props, box_size) != value

    # -------------------------------------------------------------------------
    # Property transition decisions
    # -------------------------------------------------------------------------

    def _apply_prop_animation(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        state: ResolvedRuleState,
        cause: EvaluationCause,
    ) -> bool:
        """Drive the animation for a single property. Returns True if a batched style update is needed."""
        # Cancel any pending delay.
        self._cancel_pending_delay(ctx, prop)

        # Class-change animations take priority over pseudo-state changes (hover/focus).
        if not cause.is_class_driven and prop in ctx.class_anim_props:
            return False

        resolved = self._resolve_property_transition(widget, ctx, prop, state, cause)
        if resolved is None:
            return False

        if resolved.spec is not None and self._has_uninterpolable_color_endpoint(
            prop, resolved.current, resolved.target
        ):
            return self._snap_uninterpolable_color(ctx, prop, resolved.animation, resolved.target)

        if self._natural_target_needs_no_action(ctx, prop, resolved):
            return False

        if resolved.spec is None:
            return self._apply_snap_if_needed(
                widget,
                ctx,
                prop,
                resolved.animation,
                resolved.target,
                resolved.is_natural_target,
                state,
            )

        if self._transition_should_snap(widget, prop, state, resolved.spec, cause):
            return self._snap_prop_or_effect(
                widget,
                ctx,
                prop,
                resolved.animation,
                resolved.target,
                resolved.is_natural_target,
                state.target_props,
            )

        return self._start_or_retarget_anim(
            widget,
            ctx,
            prop,
            resolved.animation,
            state,
            resolved.current,
            resolved.target,
            resolved.is_natural_target,
            resolved.spec,
            cause,
        )

    def _resolve_property_transition(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        state: ResolvedRuleState,
        cause: EvaluationCause,
    ) -> ResolvedProperty | None:
        """Resolve the current value, target value, animation, and transition for one property."""
        animation = ctx.active_animations.get(prop)
        natural_hint = self._natural_hint_from_anim(animation, ctx, prop, state, cause)
        base_raw = state.base_props.get(prop, "auto")
        current = self._resolve_current_raw(widget, ctx, prop, state.base_props, base_raw)
        target, is_natural_target = self._resolve_target_raw(
            widget,
            state.base_props,
            state.target_props,
            prop,
            natural_hint,
            current,
        )
        if not target:
            return None

        # Properties without a concrete base value appear at their target already.
        if (
            prop not in SIZE_PROPS
            and prop not in ctx.css_anim_props
            and state.base_props.get(prop) in (None, "", "auto")
        ):
            current = target

        return ResolvedProperty(
            animation=animation,
            current=current,
            target=target,
            is_natural_target=is_natural_target,
            spec=state.transitions.get(prop),
        )

    def _resolve_current_raw(
        self, widget: QWidget, ctx: WidgetContext, prop: str, base_props: dict[str, str], base_raw: str
    ) -> str:
        """Resolve the CSS value to use as the animation start point."""
        current_raw = ctx.css_anim_props.get(prop)
        if current_raw is not None:
            return current_raw
        if prop not in SIZE_PROPS:
            return base_raw

        pre_polish_size = ctx.pre_polish_size
        if "width" in prop:
            raw_px = pre_polish_size[0] if pre_polish_size is not None else widget.width()
        else:
            raw_px = pre_polish_size[1] if pre_polish_size is not None else widget.height()
        actual = content_box_px(widget, base_props, prop, raw_px)
        return f"{actual}px" if actual > 0 else base_raw

    def _resolve_target_raw(
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
            target_raw = natural_hint or self._get_natural_size(widget, base_props, prop, current_raw)
        if not target_raw:
            if prop in SIZE_PROPS:
                target_raw = natural_hint or self._get_natural_size(widget, base_props, prop, current_raw)
            elif "color" in prop:
                target_raw = "white" if prop == "color" else "transparent"
        return target_raw or "", is_natural_target

    def _natural_hint_from_anim(
        self,
        anim_obj: Animation | None,
        ctx: WidgetContext,
        prop: str,
        state: ResolvedRuleState,
        cause: EvaluationCause,
    ) -> str | None:
        """Return the stored natural size from a running GenericPropertyAnimation, or None."""
        if not (
            isinstance(anim_obj, GenericPropertyAnimation)
            and prop in ctx.css_anim_props
            and prop in SIZE_PROPS
            and not (state.target_props.get(prop) or state.base_props.get(prop))
            and not cause.is_class_driven
        ):
            return None
        return f"{anim_obj.natural_val:.3f}{anim_obj.unit}"

    @staticmethod
    def _natural_target_needs_no_action(
        ctx: WidgetContext,
        prop: str,
        resolved: ResolvedProperty,
    ) -> bool:
        """Let Qt retain a natural size when the engine has no active inline constraint."""
        if not resolved.is_natural_target or prop in ctx.css_anim_props:
            return False
        # With no animation, a class-change size snapshot still needs to animate away.
        return resolved.animation is not None or not ctx.pre_polish_size

    def _apply_snap_if_needed(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        anim_obj: Animation | None,
        target_raw: str,
        is_natural_target: bool,
        state: ResolvedRuleState,
    ) -> bool:
        """Snap an untransitioned property unless Qt already renders the desired value."""
        if (
            prop not in EFFECT_PROPS
            and not anim_obj
            and prop not in ctx.css_anim_props
            and target_raw == state.base_props.get(prop)
            and not self._needs_qt_border_radius_clamp(widget, state.target_props, prop)
        ):
            return False
        return self._snap_prop_or_effect(widget, ctx, prop, anim_obj, target_raw, is_natural_target, state.target_props)

    def _transition_should_snap(
        self,
        widget: QWidget,
        prop: str,
        state: ResolvedRuleState,
        trans: TransitionSpec,
        cause: EvaluationCause,
    ) -> bool:
        """Return whether transition policy requires an immediate target update."""
        return (
            trans.duration_ms == 0
            or not self.animations_enabled
            or cause.snaps_transitions
            or (
                cause is EvaluationCause.RULE_RELOAD
                and prop in BORDER_RADIUS_PROPS
                and self._needs_qt_border_radius_clamp(widget, state.target_props, prop)
            )
        )

    def _cancel_pending_delay(self, ctx: WidgetContext, prop: str) -> None:
        """Stop and discard the pending delay timer for prop, if any."""
        old_timer = ctx.pending_delays.pop(prop, None)
        if old_timer is None:
            return
        try:
            old_timer.stop()
            safe_disconnect(old_timer.timeout)
            old_timer.deleteLater()
        except RuntimeError:
            # Qt may destroy timers before their owning widget during app shutdown.
            pass

    # -------------------------------------------------------------------------
    # Animation execution and completion tracking
    # -------------------------------------------------------------------------

    def _start_or_retarget_anim(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        anim_obj: Animation | None,
        state: ResolvedRuleState,
        current_raw: str,
        target_raw: str,
        is_natural_target: bool,
        trans: TransitionSpec,
        cause: EvaluationCause,
    ) -> bool:
        """Create or retarget an animation; report whether a delayed value needs a style flush."""
        curve = resolve_easing_curve(trans.easing)
        is_running = anim_obj is not None and anim_obj.anim.state() == QAbstractAnimation.State.Running

        # A positive delay holds the current value until a fresh evaluation fires.
        if not is_running and trans.delay_ms > 0 and cause is not EvaluationCause.DELAY_FIRE:
            if prop not in EFFECT_PROPS:
                ctx.css_anim_props[prop] = current_raw
            self._schedule_delayed_animation(widget, ctx, prop, trans.delay_ms)
            return prop not in EFFECT_PROPS

        anim_obj = self._prepare_animation(
            widget,
            ctx,
            prop,
            anim_obj,
            current_raw,
            trans,
            curve,
            state.base_props,
        )
        if not anim_obj:
            return False

        self._set_animation_target(widget, prop, anim_obj, target_raw, is_natural_target, state.target_props)

        # Negative transition-delay support.
        if not is_running and trans.delay_ms < 0 and anim_obj.anim.state() == QAbstractAnimation.State.Running:
            anim_obj.anim.setCurrentTime(min(-trans.delay_ms, trans.duration_ms))

        self._wire_animation_callbacks(widget, ctx, prop, anim_obj, cause)

        return False

    def _prepare_animation(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        animation: Animation | None,
        current_raw: str,
        spec: TransitionSpec,
        curve: QEasingCurve,
        base_props: dict[str, str],
    ) -> Animation | None:
        """Create a property's animation or update the timing of the existing one."""
        if animation is not None:
            animation.update_spec(spec.duration_ms, curve)
            return animation

        animation = self._create_animation_obj(widget, prop, current_raw, spec.duration_ms, curve, base_props)
        if animation is not None:
            ctx.active_animations[prop] = animation
        return animation

    @staticmethod
    def _set_animation_target(
        widget: QWidget,
        prop: str,
        animation: Animation,
        target_raw: str,
        is_natural_target: bool,
        target_props: dict[str, str],
    ) -> None:
        """Configure the target value and box-model inputs for an animation."""
        if not isinstance(animation, GenericPropertyAnimation):
            animation.set_target(target_raw)
            return

        animation.update_box_props(target_props)
        box_size = target_border_radius_box_size(widget, target_props) if prop in BORDER_RADIUS_PROPS else None
        animation.set_target(target_raw, clean_on_finish=is_natural_target, box_size=box_size)

    def _wire_animation_callbacks(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        animation: Animation,
        cause: EvaluationCause,
    ) -> None:
        """Attach the completion behavior required by the current evaluation cause."""
        if animation.anim.state() != QAbstractAnimation.State.Running:
            return
        if cause.is_class_driven:
            self._wire_class_anim_callback(widget, ctx, prop, animation)
        if cause.is_clicked_driven and prop in ctx.clicked_anim_props:
            self._wire_clicked_anim_callback(widget, ctx, prop, animation)

    def _wire_class_anim_callback(self, widget: QWidget, ctx: WidgetContext, prop: str, anim_obj: Animation) -> None:
        """Register finished callback for class changes."""
        ctx.class_anim_props.add(prop)
        gen = ctx.class_anim_gen
        wid = id(widget)

        def _on_done(_w: QWidget = widget, _p: str = prop, _wid: int = wid, _gen: int = gen) -> None:
            c = self._contexts.get(_wid)
            if c and _gen == c.class_anim_gen and _p in c.class_anim_props:
                c.class_anim_props.discard(_p)
                self._evaluate_widget_state(_w, cause=EvaluationCause.CLASS_ANIMATION_FINISH)

        self._replace_finished_callback(anim_obj, prop, ctx.class_anim_callbacks, _on_done)

    def _wire_clicked_anim_callback(self, widget: QWidget, ctx: WidgetContext, prop: str, anim_obj: Animation) -> None:
        """Register finished callback for clicked animations."""
        gen = ctx.clicked_anim_gen
        wid = id(widget)

        def _on_done(_w: QWidget = widget, _p: str = prop, _wid: int = wid, _gen: int = gen) -> None:
            c = self._contexts.get(_wid)
            if c and _gen == c.clicked_anim_gen and _p in c.clicked_anim_props:
                c.clicked_anim_props.discard(_p)
                if not c.clicked_anim_props:
                    self._deactivate_clicked(_w, _wid, _gen)

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

    # -------------------------------------------------------------------------
    # Orphaned animation cleanup
    # -------------------------------------------------------------------------

    def _cleanup_orphans(self, ctx: WidgetContext, state: ResolvedRuleState) -> bool:
        """Snap/stop animations for props no longer covered by any rule."""
        for prop in list(ctx.pending_delays):
            if prop not in state.animated_props:
                self._cancel_pending_delay(ctx, prop)

        needs_update = False
        for prop, orphan in list(ctx.active_animations.items()):
            if prop in state.animated_props:
                continue
            if self._remove_orphan_animation(ctx, state, prop, orphan):
                needs_update = True

        if self._evict_stale_snapped_props(ctx, state):
            needs_update = True
        return needs_update

    def _remove_orphan_animation(
        self,
        ctx: WidgetContext,
        state: ResolvedRuleState,
        prop: str,
        orphan: Animation,
    ) -> bool:
        """Settle and release one animation whose property is no longer engine-managed."""
        ctx.class_anim_props.discard(prop)
        if (old_callback := ctx.class_anim_callbacks.pop(prop, None)) is not None:
            safe_disconnect(orphan.anim.finished, old_callback)

        snap_target, is_natural_snap = self._resolve_orphan_snap_target(orphan, state, prop)
        if snap_target:
            self._snap_orphan(ctx, prop, orphan, snap_target, is_natural_snap)
        else:
            self._stop_orphan_effect(orphan)

        del ctx.active_animations[prop]
        orphan.deleteLater()
        return not isinstance(orphan, (OpacityAnimation, BoxShadowHandle))

    @staticmethod
    def _resolve_orphan_snap_target(
        orphan: Animation,
        state: ResolvedRuleState,
        prop: str,
    ) -> tuple[str | None, bool]:
        """Find the base value an orphan should settle on, including natural size fallback."""
        snap_target = state.base_props.get(prop)
        if snap_target == "auto":
            snap_target = None
        is_natural_snap = not snap_target and prop in SIZE_PROPS
        if is_natural_snap:
            snap_target = get_preferred_size_fallback(orphan.widget, state.base_props, prop)
        return snap_target, is_natural_snap

    def _snap_orphan(
        self,
        ctx: WidgetContext,
        prop: str,
        orphan: Animation,
        snap_target: str,
        is_natural_snap: bool,
    ) -> None:
        """Move an orphaned animation to its final base or natural value."""
        if isinstance(orphan, ColorAnimation) and not self._is_interpolable_color_value(snap_target):
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
            apply_shadow_to_widget(orphan.widget, None, self.effect_priority)
        elif isinstance(orphan, OpacityAnimation):
            try:
                orphan.widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

    @staticmethod
    def _evict_stale_snapped_props(ctx: WidgetContext, state: ResolvedRuleState) -> bool:
        """Remove inline values that have neither a matching rule nor a live animation."""
        stale_props = {
            prop
            for prop in ctx.css_anim_props
            if prop not in state.animated_props and prop not in ctx.active_animations and prop not in state.base_props
        }
        for prop in stale_props:
            del ctx.css_anim_props[prop]
        return bool(stale_props)

    # -------------------------------------------------------------------------
    # Animation and value helpers
    # -------------------------------------------------------------------------

    def _get_natural_size(
        self, widget: QWidget, base_props: dict[str, str], prop: str, current_raw: str | None = None
    ) -> str:
        return get_natural_size(widget, self._ctx(widget), base_props, prop, current_raw)

    def _is_animatable(self, prop: str) -> bool:
        """Return True if the engine knows how to animate this CSS property."""
        return self._is_color_prop(prop) or prop in EFFECT_PROPS or prop in SUPPORTED_NUMERIC_PROPS

    @staticmethod
    def _is_color_prop(prop: str) -> bool:
        """Return True for QSS color properties handled by ColorAnimation."""
        return prop == "color" or prop.endswith("-color")

    @staticmethod
    def _is_interpolable_color_value(value: str) -> bool:
        """Return True when value is a solid color ColorAnimation can interpolate."""
        return parse_color(value).isValid()

    def _has_uninterpolable_color_endpoint(self, prop: str, current_raw: str, target_raw: str) -> bool:
        """Return True when a color transition contains a static-only endpoint such as a gradient."""
        if not self._is_color_prop(prop):
            return False
        return not self._is_interpolable_color_value(current_raw) or not self._is_interpolable_color_value(target_raw)

    def _snap_uninterpolable_color(
        self, ctx: WidgetContext, prop: str, anim_obj: Animation | None, target_raw: str
    ) -> bool:
        """Snap a color prop when either endpoint is not a solid color."""
        if isinstance(anim_obj, ColorAnimation):
            anim_obj.anim.stop()
        if self._is_interpolable_color_value(target_raw):
            if isinstance(anim_obj, ColorAnimation):
                anim_obj.snap_to(target_raw)
            else:
                ctx.css_anim_props[prop] = target_raw
            return True
        if prop in ctx.css_anim_props:
            del ctx.css_anim_props[prop]
            return True
        return False

    def _schedule_delayed_animation(self, widget: QWidget, ctx: WidgetContext, prop: str, delay_ms: int) -> None:
        """Schedule prop's animation to start after delay_ms."""
        wid = id(widget)
        timer = QTimer(self)
        timer.setSingleShot(True)

        def _fire(_w: QWidget = widget, _p: str = prop, _wid: int = wid) -> None:
            c = self._contexts.get(_wid)
            if c is not None:
                c.pending_delays.pop(_p, None)
            try:
                self._fire_delayed_prop(_w, _p)
            except RuntimeError:
                pass

        timer.timeout.connect(_fire)
        ctx.pending_delays[prop] = timer
        timer.start(delay_ms)

    def _fire_delayed_prop(self, widget: QWidget, prop: str) -> None:
        """Re-evaluate a single property after its transition-delay has elapsed."""
        if not self._should_evaluate(widget):
            return
        ctx = self._ctx(widget)
        state = self._collect_rule_state(widget, ctx)
        ctx.style_box_props = dict(state.target_props)
        if prop not in state.animated_props:
            return
        needs_update = self._apply_prop_animation(widget, ctx, prop, state, EvaluationCause.DELAY_FIRE)
        if needs_update:
            self._flush_widget_style_now(widget, ctx)

    def _snap_prop_or_effect(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        anim_obj: Animation | None,
        target_raw: str,
        is_natural_target: bool,
        target_props: dict[str, str],
    ) -> bool:
        """Snap a property to its target value instantly. Returns True if a batched style update is needed."""
        if anim_obj is not None:
            self._snap_existing_animation(widget, prop, anim_obj, target_raw, is_natural_target, target_props)
            return not isinstance(anim_obj, (OpacityAnimation, BoxShadowHandle))

        if prop in EFFECT_PROPS:
            new_anim = self._create_animation_obj(widget, prop, target_raw, 0, QEasingCurve.Type.Linear, target_props)
            if new_anim is not None:
                ctx.active_animations[prop] = new_anim
            return False

        if is_natural_target:
            ctx.css_anim_props.pop(prop, None)
            return True

        ctx.css_anim_props[prop] = self._clamp_static_numeric_target(widget, prop, target_raw, target_props)
        return True

    @staticmethod
    def _snap_existing_animation(
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
    def _clamp_static_numeric_target(
        widget: QWidget,
        prop: str,
        target_raw: str,
        target_props: dict[str, str],
    ) -> str:
        """Clamp a static numeric value when Qt's box geometry requires it."""
        parsed = parse_css_numeric(target_raw)
        if parsed is None:
            return target_raw

        value, unit = parsed
        box_size = target_border_radius_box_size(widget, target_props)
        clamped = clamp_border_radius(widget, prop, max(0.0, value), unit, target_props, box_size)
        if clamped != value and prop in NON_NEGATIVE_PROPS:
            return f"{clamped:.3f}{unit}"
        return target_raw

    def _apply_cursor(self, widget: QWidget, ctx: WidgetContext, target_props: dict[str, str]) -> None:
        """Apply the CSS cursor value to widget via setCursor() / unsetCursor()."""
        cursor_val = target_props.get("cursor")
        desired = cursor_val if cursor_val in CURSOR_MAP else None
        if desired == ctx.applied_cursor:
            return
        if desired is not None:
            widget.setCursor(CURSOR_MAP[desired])
        else:
            widget.unsetCursor()
        ctx.applied_cursor = desired

    def _create_animation_obj(
        self,
        widget: QWidget,
        prop: str,
        initial_raw: str,
        duration_ms: int,
        curve: QEasingCurve | QEasingCurve.Type,
        box_props: dict[str, str] | None = None,
    ) -> Animation | None:
        """Instantiate the correct Animation subclass for a CSS property."""
        ctx = self._ctx(widget)
        if "color" in prop:
            return ColorAnimation(
                widget,
                prop,
                initial_raw,
                duration_ms,
                curve,
                self,
                ctx=ctx,
                style_flush_callback=self._schedule_style_flush,
            )
        if prop == "opacity":
            return OpacityAnimation(
                widget,
                parse_css_val(initial_raw) or 0,
                duration_ms,
                curve,
                self,
                self.effect_priority,
            )
        if prop == "box-shadow":
            return BoxShadowHandle(widget, initial_raw, duration_ms, curve, self, self.effect_priority)
        if prop in SUPPORTED_NUMERIC_PROPS:
            parsed = parse_css_numeric(initial_raw)
            if parsed is not None:
                start_val, unit = parsed
                return GenericPropertyAnimation(
                    widget,
                    prop,
                    start_val,
                    duration_ms,
                    curve,
                    self,
                    unit=unit,
                    ctx=ctx,
                    box_props=box_props,
                    style_flush_callback=self._schedule_style_flush,
                )
        return None

    # -------------------------------------------------------------------------
    # Rule hot-reload
    # -------------------------------------------------------------------------

    def reload_rules(self, rules: list[StyleRule]) -> None:
        """Hot-reload CSS rules, clearing old animations and engine-owned inline styles."""
        animated_widgets, inline_widget_ids = self._collect_reload_widgets()
        for ctx in list(self._contexts.values()):
            self._reset_context_for_reload(ctx)

        self._matcher.rules = rules
        self._matcher.build_quick_filters()
        self._matcher.clear_caches()

        animated_widget_ids = self._clear_reload_styles(animated_widgets)
        effect_only_widgets = {widget for widget in animated_widgets if id(widget) not in inline_widget_ids}
        QTimer.singleShot(
            0,
            lambda: self._reeval_reload_widgets_deferred(effect_only_widgets, animated_widget_ids),
        )

    def _collect_reload_widgets(self) -> tuple[set[QWidget], set[int]]:
        """Return live widgets that need special handling during a stylesheet reload."""
        animated_widgets: set[QWidget] = set()
        inline_widget_ids: set[int] = set()
        for widget_id, ctx in list(self._contexts.items()):
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
                    inline_widget_ids.add(widget_id)
            except RuntimeError:
                pass

        return animated_widgets, inline_widget_ids

    def _reset_context_for_reload(self, ctx: WidgetContext) -> None:
        """Discard transient animation state before the new rule set is installed."""
        self._cancel_all_pending_delays(ctx)
        self._disconnect_finished_callbacks(ctx, ctx.class_anim_callbacks)
        self._disconnect_finished_callbacks(ctx, ctx.clicked_anim_callbacks)
        ctx.class_anim_props.clear()
        ctx.clicked_anim_props.clear()
        ctx.active_pseudos.discard(":clicked")
        ctx.class_anim_gen += 1
        ctx.clicked_anim_gen += 1
        self._stop_animations(ctx, clear_effects=True)

    def _clear_reload_styles(self, animated_widgets: set[QWidget]) -> set[int]:
        """Remove engine-owned inline styles and return the ids reset during the reload."""
        animated_widget_ids: set[int] = set()
        for widget in animated_widgets:
            try:
                animated_widget_ids.add(id(widget))
                ctx = self._ctx(widget)
                ctx.css_anim_props.clear()
                ctx.active_pseudos.clear()
                widget.setStyleSheet("")
            except RuntimeError:
                pass

        app = QApplication.instance()
        if isinstance(app, QApplication):
            for widget in app.allWidgets():
                if id(widget) in animated_widget_ids:
                    continue
                try:
                    ctx = self._contexts.get(id(widget))
                    if ctx is not None and ctx.css_anim_props:
                        ctx.css_anim_props.clear()
                        widget.setStyleSheet("")
                except RuntimeError:
                    pass

        return animated_widget_ids

    def _reeval_reload_widgets_deferred(self, effect_only_widgets: set[QWidget], prev_animated_ids: set[int]) -> None:
        """Re-evaluate widgets that need engine-managed state after a hot-reload stylesheet change."""
        for widget in effect_only_widgets:
            try:
                widget.objectName()
                ctx = self._contexts.get(id(widget))
                if ctx is not None and ctx.active_animations:
                    continue
                if self._should_evaluate(widget):
                    self._evaluate_widget_state(widget, cause=EvaluationCause.RULE_RELOAD)
                else:
                    widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
        if not self._matcher.has_effect_rules and not self._matcher.has_border_radius_rules:
            return

        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        for widget in app.allWidgets():
            ctx = self._contexts.get(id(widget))
            if id(widget) in prev_animated_ids:
                continue
            if ctx is not None and ctx.active_animations:
                continue
            if self._should_evaluate(widget):
                self._evaluate_widget_state(widget, cause=EvaluationCause.RULE_RELOAD)
        if self._matcher.has_border_radius_rules:
            QTimer.singleShot(0, self._reeval_border_radius_widgets_after_reload)

    def _reeval_border_radius_widgets_after_reload(self) -> None:
        """Re-evaluate border-radius widgets after reload Polish/layout has had one more event-loop turn."""
        if not self._matcher.has_border_radius_rules:
            return
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        for widget in app.allWidgets():
            try:
                widget.objectName()
                if not self._should_evaluate(widget):
                    continue
                if not any(
                    prop in BORDER_RADIUS_PROPS
                    for rule in self._matcher.matching_rules(widget)
                    for prop in rule.properties
                ):
                    continue
                ctx = self._contexts.get(id(widget))
                if ctx is not None and ctx.active_animations:
                    continue
                self._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
