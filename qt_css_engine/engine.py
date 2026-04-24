import logging
import os
import re
from typing import TYPE_CHECKING

from .constants import CURSOR_MAP, EASING_MAP, EFFECT_PROPS, PSEUDO_EVENTS, SIZE_PROPS, SUPPORTED_NUMERIC_PROPS
from .handlers import (
    BoxShadowHandle,
    ColorAnimation,
    GenericPropertyAnimation,
    OpacityAnimation,
)
from .qt_compat.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, Qt, QTimer
from .qt_compat.QtGui import QMouseEvent
from .qt_compat.QtWidgets import QAbstractButton, QApplication, QWidget
from .types import Animation, EvaluationCause, InternalWriteReason, WidgetContext
from .utils import (
    apply_shadow_to_widget,
    content_box_px,
    get_preferred_size_fallback,
    make_cubic_bezier_curve,
    make_steps_curve,
    parse_css_numeric,
    parse_css_val,
    scoped_anim_style,
)

if TYPE_CHECKING:
    from qt_css_engine.css_parser import StyleRule, TransitionSpec


event_logger = logging.getLogger("qt_css_engine.event")

_CUBIC_BEZIER_RE = re.compile(
    r"cubic-bezier\(\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*\)",
    re.IGNORECASE,
)

_STEPS_RE = re.compile(
    r"steps\(\s*(\d+)(?:\s*,\s*(jump-start|jump-end|jump-none|jump-both|start|end))?\s*\)",
    re.IGNORECASE,
)


class TransitionEngine(QObject):
    """
    Core CSS transition engine for PyQt6/PySide6.

    Installed as a global event filter on QApplication. Intercepts hover, mouse,
    and focus events to track widget pseudo-states, evaluates the CSS cascade,
    and drives smooth property animations via Qt's animation framework.
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
        self.rules = rules
        if startup_delay_ms <= 0:
            self.animations_enabled = True
        else:
            self.animations_enabled = False
            QTimer.singleShot(startup_delay_ms, lambda: self._on_startup_done())
        # One source of truth for all per-widget state.
        self._contexts: dict[int, WidgetContext] = {}
        # Widgets that have at least one :active rule — populated at Polish time for O(1) activate/deactivate.
        self._active_rule_widgets: dict[int, QWidget] = {}
        # Widget IDs with a destroyed-signal connection (prevents double-connect).
        self._connected_widgets: set[int] = set()
        # Checkable widget IDs already connected to toggled signal.
        self._connected_checkable_ids: set[int] = set()
        # Rule-match cache: widget id → list of matching rules.
        self._rule_cache: dict[int, list[StyleRule]] = {}
        # Quick filters: sets of segments[-1] parts that have transitions or effect props
        self._animated_tags: set[str] = set()
        self._animated_classes: set[str] = set()
        self._animated_ids: set[str] = set()
        # True if any rule uses effect props (opacity, box-shadow) — these need engine init at base state
        self._has_effect_rules: bool = False
        # True if any rule declares a cursor — Qt QSS ignores cursor, so the engine must apply it.
        self._has_cursor_rules: bool = False
        # Enable event logging if the CSS_ENGINE_EVENT_LOGGING env var is set.
        if os.environ.get("CSS_ENGINE_EVENT_LOGGING", "").lower() not in ("1", "true", "yes"):
            event_logger.disabled = True
        # When True, middle/right clicks are ignored entirely (no :pressed/:clicked animations).
        # Controlled by CSS_ENGINE_LEFT_CLICK_ONLY env var.
        self._left_click_only: bool = os.environ.get("CSS_ENGINE_LEFT_CLICK_ONLY", "").lower() in ("1", "true", "yes")
        # Timestamp of the last non-left mouse press event claimed by a widget with matching rules.
        # Prevents :pressed from propagating to ancestor widgets on middle/right click.
        self._claimed_mouse_event_ts: int = -1
        self._build_quick_filters()

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
            self._connect_destroyed(widget)
        return ctx

    # -------------------------------------------------------------------------
    # Event filtering and pseudo-state tracking
    # -------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore
        """Intercept widget events to track pseudo-states and trigger CSS transitions."""
        if not isinstance(watched, QWidget):
            return False
        t = event.type()
        if t == QEvent.Type.Polish:
            self._on_polish(watched)
        elif t == QEvent.Type.DynamicPropertyChange:
            prop_name = getattr(event, "propertyName", lambda: None)()
            if prop_name is not None and getattr(prop_name, "data", lambda: b"")() == b"class":
                self._on_class_change(watched)
        elif t == QEvent.Type.WindowActivate:
            self._on_window_activate(watched)
        elif t == QEvent.Type.WindowDeactivate:
            self._on_window_deactivate(watched)
        elif t == QEvent.Type.Leave and watched.isWindow():
            # Force-clear :hover/:pressed if focus is shifted to a popup window.
            self._on_window_deactivate(watched, clear_active=False)
        elif t in PSEUDO_EVENTS:
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                if isinstance(event, QMouseEvent) and event.button() != Qt.MouseButton.LeftButton:
                    if self._left_click_only:
                        return False
                    ts = event.timestamp()
                    if ts == self._claimed_mouse_event_ts:
                        return False
                    if not self._should_evaluate(watched):
                        return False
                    self._claimed_mouse_event_ts = ts
            ctx = self._ctx(watched)
            updated = self._update_pseudos(ctx.active_pseudos, t)
            cause = EvaluationCause.PSEUDO_STATE
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                cause = self._prepare_clicked(watched, ctx, updated)
            if updated != ctx.active_pseudos:
                ctx.active_pseudos = updated
                self._evaluate_widget_state(watched, cause=cause)
                if cause is EvaluationCause.CLICKED_ACTIVATION:
                    self._finish_clicked_activation(watched, ctx)
        return False

    def _on_polish(self, widget: QWidget) -> None:
        """Handle Polish events — evaluate initial widget state on first polish."""
        ctx = self._contexts.get(id(widget))
        # Ignore Polish events triggered by our own internal style writes (e.g. during class change unpolish/polish,
        # or natural size calculation).
        if ctx is not None and ctx.internal_write_depth > 0:
            return
        # Wire up the toggled signal for checkable widgets (idempotent).
        self._connect_checkable(widget)
        # Ensure Qt delivers HoverEnter/HoverLeave for widgets matched by :hover rules.
        # Must happen before the active_animations guard so it fires even during running animations.
        self._ensure_wa_hover(widget)
        self._seed_active_pseudo(widget)
        # Deferred Polish events arrive after _get_natural_size's setStyleSheet calls restore the
        # inline style.  At that point animations are already running — snapping them would kill the
        # transition.  Any class-change or pseudo-state re-evaluation that truly matters is driven
        # by _on_class_change / _evaluate_widget_state directly; Polish is only needed for initial
        # widget setup (active_animations is empty then).
        if ctx is not None and ctx.active_animations:
            return
        self._evaluate_widget_state(widget, cause=EvaluationCause.POLISH)

    def _on_class_change(self, widget: QWidget) -> None:
        """Handle class property change — snapshot size, unpolish/polish, and kick off animations."""
        # Invalidate rule cache first so _should_evaluate sees the fresh class set.
        self._rule_cache.clear()
        # Skip widgets that no animated rule could touch. Without this guard, unpolish/polish
        # below runs on every widget in the app that ever gets a class property change, which
        # can interact badly with widget-level setStyle() in the presence of an app stylesheet.
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
        # Fresh generation — stale finished callbacks from prior class changes become no-ops.
        ctx.class_anim_gen += 1
        ctx.class_anim_props.clear()
        self._evaluate_widget_state(widget, cause=EvaluationCause.CLASS_CHANGE)
        ctx.pre_polish_size = None

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
        # Clear stuck :hover/:pressed so widgets don't remain frozen in the highlighted state.
        # clear_active=False when called from the Leave path: window is still focused, only cursor left.
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
        if not any(":clicked" in rule.pseudo_set for rule in self._matching_rules(widget)):
            return EvaluationCause.PSEUDO_STATE
        updated.add(":clicked")
        ctx.clicked_anim_gen += 1
        ctx.clicked_anim_props.clear()
        for rule in self._matching_rules(widget):
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
        """
        Set WA_Hover on widget if it matches any rule with a :hover pseudo-class.

        Qt only generates HoverEnter/HoverLeave events when this attribute is set.
        Native widgets (QPushButton, QLabel, …) typically receive it automatically
        when a QSS stylesheet is applied, but plain QWidget subclasses and widgets
        that only inherit the app stylesheet do not.  The engine must set it
        explicitly so its event-filter can track :hover state reliably.
        """
        if widget.testAttribute(Qt.WidgetAttribute.WA_Hover):
            return  # already set — skip rule-matching cost on every subsequent polish
        if not self._should_evaluate(widget):
            return
        if any(":hover" in rule.pseudo_set for rule in self._matching_rules(widget)):
            widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _seed_active_pseudo(self, widget: QWidget) -> None:
        """Add :active to the widget's pseudo set at Polish time if its window is currently active."""
        if not any(":active" in r.pseudo_set for r in self._matching_rules(widget)):
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
    # Quick-filter construction
    # -------------------------------------------------------------------------

    def _build_quick_filters(self) -> None:
        """Rebuild animated tag/class/id sets from current rules for fast pre-filtering."""
        self._animated_tags.clear()
        self._animated_classes.clear()
        self._animated_ids.clear()
        self._has_effect_rules = False
        self._has_cursor_rules = False

        for rule in self.rules:
            if rule.subcontrol:
                continue
            has_effect_props = any(p in EFFECT_PROPS for p in rule.properties)
            has_cursor_props = "cursor" in rule.properties
            if not rule.transitions and not has_effect_props and not has_cursor_props:
                continue
            if has_effect_props or any(t.prop in ("opacity", "all") for t in rule.transitions):
                self._has_effect_rules = True
            if has_cursor_props:
                self._has_cursor_rules = True
            last_segment = rule.segments[-1]
            if last_segment.startswith("#"):
                self._animated_ids.add(last_segment.split(".")[0][1:])
            elif last_segment.startswith("."):
                for cls in last_segment.split(".")[1:]:
                    self._animated_classes.add(cls)
            else:
                parts = last_segment.split(".")
                if parts[0]:
                    self._animated_tags.add(parts[0])
                for cls in parts[1:]:
                    self._animated_classes.add(cls)

    # -------------------------------------------------------------------------
    # Widget selector matching
    # -------------------------------------------------------------------------

    def _should_evaluate(self, widget: QWidget) -> bool:
        """Return True if the widget could be affected by any animated CSS rule."""
        ctx = self._contexts.get(id(widget))
        if ctx is not None and ctx.active_animations:
            return True
        if self._animated_ids and widget.objectName() in self._animated_ids:
            return True
        if self._animated_tags and type(widget).__name__ in self._animated_tags:
            return True
        if self._animated_classes:
            if any(cls in self._animated_classes for cls in self._widget_classes(widget)):
                return True
        return False

    @staticmethod
    def _widget_classes(widget: QWidget) -> list[str]:
        """Return the CSS class tokens from the widget's 'class' property."""
        raw: str = widget.property("class") or ""
        return raw.split()

    def _widget_matches_segment(self, widget: QWidget, segment: str) -> bool:
        """Return True if widget matches a single selector segment (id, class, or tag)."""
        if segment.startswith("#"):
            parts = segment.split(".")
            if widget.objectName() != parts[0][1:]:
                return False
            if len(parts) > 1:
                return all(cls in self._widget_classes(widget) for cls in parts[1:])
            return True
        if segment.startswith("."):
            parts = segment.split(".")
            return all(cls in self._widget_classes(widget) for cls in parts[1:])
        parts = segment.split(".")
        tag_name = parts[0]
        if tag_name and type(widget).__name__ != tag_name:
            return False
        if len(parts) > 1:
            return all(cls in self._widget_classes(widget) for cls in parts[1:])
        return True

    def _matches(self, widget: QWidget, rule: StyleRule) -> bool:
        """Return True if widget matches a full descendant-combinator selector."""
        segments = rule.segments
        if not segments:
            return False
        if not self._widget_matches_segment(widget, segments[-1]):
            return False
        if len(segments) == 1:
            return True
        seg_idx = len(segments) - 2
        ancestor: QObject | None = widget.parent()
        while ancestor and seg_idx >= 0:
            if isinstance(ancestor, QWidget) and self._widget_matches_segment(ancestor, segments[seg_idx]):
                seg_idx -= 1
            ancestor = ancestor.parent()
        return seg_idx < 0

    def _matching_rules(self, widget: QWidget) -> list[StyleRule]:
        """
        Return rules matching widget, using per-widget cached results when possible.

        Keyed by id(widget) rather than a type/class signature so that widgets with the
        same CSS class but different ancestors each get their own correct cache entry.
        """
        wid = id(widget)
        cached = self._rule_cache.get(wid)
        if cached is not None:
            return cached
        result = [rule for rule in self.rules if self._matches(widget, rule)]
        self._rule_cache[wid] = result
        return result

    # -------------------------------------------------------------------------
    # Widget lifecycle tracking
    # -------------------------------------------------------------------------

    def _connect_destroyed(self, widget: QWidget) -> None:
        """Connect widget.destroyed to the cleanup handler (idempotent)."""
        if id(widget) in self._connected_widgets:
            return
        self._connected_widgets.add(id(widget))
        widget.destroyed.connect(lambda: self._on_widget_destroyed(widget))

    def _on_widget_destroyed(self, widget: QWidget) -> None:
        """Remove all engine state for a destroyed widget and stop its animations."""
        wid = id(widget)
        self._connected_widgets.discard(wid)
        self._connected_checkable_ids.discard(wid)
        self._rule_cache.pop(wid, None)
        self._active_rule_widgets.pop(wid, None)
        ctx = self._contexts.pop(wid, None)
        if ctx is None:
            return
        for timer in ctx.pending_delays.values():
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        ctx.pending_delays.clear()
        for prop, cb in ctx.class_anim_callbacks.items():
            anim_obj = ctx.active_animations.get(prop)
            if anim_obj is not None:
                try:
                    anim_obj.anim.finished.disconnect(cb)
                except RuntimeError, TypeError:
                    pass
        ctx.class_anim_callbacks.clear()
        for prop, cb in ctx.clicked_anim_callbacks.items():
            anim_obj = ctx.active_animations.get(prop)
            if anim_obj is not None:
                try:
                    anim_obj.anim.finished.disconnect(cb)
                except RuntimeError, TypeError:
                    pass
        ctx.clicked_anim_callbacks.clear()
        ctx.clicked_anim_props.clear()
        for anim_obj in ctx.active_animations.values():
            try:
                anim_obj.anim.stop()
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

        # Fast path for brand-new widgets being polished in their base state:
        # Qt's app stylesheet already has all base-state values (animated props are only
        # stripped from pseudo-state blocks in the cleaned QSS, not from the base block).
        # Effect props (opacity, box-shadow) need their QGraphicsEffect initialised even at
        # base state, so skip only when there are no effect rules at all.
        if (
            cause.snaps_transitions
            and not ctx.css_anim_props
            and not ctx.active_animations
            and not self._has_effect_rules
            and not self._has_cursor_rules
        ):
            return
        (
            base_props,
            target_props,
            target_transitions,
            all_animated_props,
        ) = self._collect_rule_state(widget, ctx)
        needs_style_update = False
        for prop in all_animated_props:
            if self._apply_prop_animation(widget, ctx, prop, base_props, target_props, target_transitions, cause):
                needs_style_update = True
        if self._cleanup_orphans(widget, ctx, all_animated_props, base_props):
            needs_style_update = True
        if needs_style_update:
            event_logger.debug("Updating style: %s", widget)
            widget.setStyleSheet(scoped_anim_style(widget, ctx.css_anim_props))
        self._apply_cursor(widget, ctx, target_props)

    def _collect_rule_state(
        self, widget: QWidget, ctx: WidgetContext
    ) -> tuple[dict[str, str], dict[str, str], dict[str, TransitionSpec], set[str]]:
        """
        Evaluate the CSS cascade for widget.

        Returns (base_props, target_props, target_transitions, all_animated_props).
        CSS cascade semantics: most-specific pseudo wins; equal specificity uses last-in-stylesheet order.
        """
        base_props: dict[str, str] = {}
        target_props: dict[str, str] = {}
        target_transitions: dict[str, TransitionSpec] = {}
        trans_priority: dict[str, int] = {}
        all_animated_props: set[str] = set()
        pseudos = ctx.active_pseudos
        for rule in self._matching_rules(widget):
            rule_in_target = not rule.pseudo_set or rule.pseudo_set <= pseudos
            priority = sum(self.pseudo_priority.get(p, 0) for p in rule.pseudo_set) if rule_in_target else -1
            for trans in rule.transitions:
                all_animated_props.add(trans.prop)
                if rule_in_target and priority >= trans_priority.get(trans.prop, -1):
                    target_transitions[trans.prop] = trans
                    trans_priority[trans.prop] = priority
            if not rule.pseudo_set:
                base_props.update(rule.properties)
            if rule_in_target:
                target_props.update(rule.properties)
        # Expand `transition: all` to every animatable property present in base/target.
        if "all" in all_animated_props:
            all_spec = target_transitions.pop("all", None)
            all_animated_props.discard("all")
            for p in set(base_props) | set(target_props):
                if self._is_animatable(p):
                    all_animated_props.add(p)
                    if p not in target_transitions and all_spec is not None:
                        target_transitions[p] = all_spec
            # Engine-managed props set by a prior class-change may be absent from current rules
            # (they're reverting to natural). `transition: all` must animate the return trip too.
            if all_spec is not None:
                engine_managed: set[str] = set(ctx.css_anim_props)
                engine_managed |= set(ctx.active_animations)
                for p in engine_managed:
                    if p not in all_animated_props and self._is_animatable(p) and p not in EFFECT_PROPS:
                        all_animated_props.add(p)
                        target_transitions[p] = all_spec
        # Effect props need engine handling even without a transition declaration.
        for p in EFFECT_PROPS:
            if p in base_props or p in target_props:
                all_animated_props.add(p)
        return base_props, target_props, target_transitions, all_animated_props

    def _apply_prop_animation(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        base_props: dict[str, str],
        target_props: dict[str, str],
        target_transitions: dict[str, TransitionSpec],
        cause: EvaluationCause,
    ) -> bool:
        """Drive the animation for a single property. Returns True if a batched style update is needed."""
        # Cancel any pending delay for this prop unconditionally — the widget state has changed
        # (new pseudo, class-change, window deactivate, etc.) and a fresh evaluation is in progress.
        old_timer = ctx.pending_delays.pop(prop, None)
        if old_timer is not None:
            old_timer.stop()
            old_timer.deleteLater()

        # Class-change animations take priority over pseudo-state changes (hover/focus).
        # Defer until the class-change animation finishes, then re-evaluate picks up hover.
        if not cause.is_class_driven and prop in ctx.class_anim_props:
            return False

        target_raw, is_natural_target = self._resolve_target_raw(widget, base_props, target_props, prop)
        if not target_raw:
            return False

        base_raw = base_props.get(prop)
        if not base_raw or base_raw == "auto":
            base_raw = get_preferred_size_fallback(widget, base_props, prop) if prop in SIZE_PROPS else target_raw

        anim_obj = ctx.active_animations.get(prop)

        # Natural-state target with nothing running: Qt lays out at natural size without intervention.
        # Exception: if a frozen inline constraint exists (written during a pending delay), we must
        # animate to clear it — Qt cannot reach the natural size while the inline style constrains it.
        if is_natural_target and not anim_obj and not ctx.pre_polish_size and prop not in ctx.css_anim_props:
            return False

        # clean_on_finish already removed this prop — animation completed successfully and the
        # widget is at its natural layout size.  Any re-evaluation triggered by _on_class_anim_done
        # must not restart the animation toward get_preferred_size_fallback (sizeHint), which would
        # be wrong for stretch-fill widgets whose natural width > sizeHint.
        if is_natural_target and anim_obj and prop not in ctx.css_anim_props:
            return False

        # No transition declared → snap.
        if prop not in target_transitions:
            return self._snap_prop_or_effect(widget, ctx, prop, anim_obj, target_raw, is_natural_target)

        trans = target_transitions[prop]
        if trans.duration_ms == 0 or not self.animations_enabled or cause.snaps_transitions:
            return self._snap_prop_or_effect(widget, ctx, prop, anim_obj, target_raw, is_natural_target)

        # Animated path: create or update animation object, then point it at the new target.
        curve = self._resolve_easing_curve(trans.easing)
        is_running = anim_obj is not None and anim_obj.anim.state() == QAbstractAnimation.State.Running

        if not is_running:
            # Delay applies on both fresh starts and re-starts of stopped/finished animations.
            # Only re-targeting an actively running animation skips the delay.
            if trans.delay_ms > 0 and cause is not EvaluationCause.DELAY_FIRE:
                # Freeze the current rendered value so Qt doesn't immediately apply the new
                # class/state values during the delay period (class-based QSS rules are not
                # stripped, so without this the widget would visually snap to the new state).
                # Effect props (opacity, box-shadow) are managed via QGraphicsEffect — no inline
                # style needed, and their value is not held in css_anim_props.
                if prop not in EFFECT_PROPS:
                    current_raw = self._resolve_current_raw(widget, ctx, prop, base_props, base_raw)
                    ctx.css_anim_props[prop] = current_raw
                self._schedule_delayed_animation(widget, ctx, prop, trans.delay_ms)
                return prop not in EFFECT_PROPS  # needs setStyleSheet iff we wrote css_anim_props
            if anim_obj is None:
                current_raw = self._resolve_current_raw(widget, ctx, prop, base_props, base_raw)
                anim_obj = self._create_animation_obj(widget, prop, current_raw, trans.duration_ms, curve)
                if anim_obj:
                    self._register_animation(widget, ctx, prop, anim_obj)
            else:
                # Stopped animation — update spec before re-targeting.
                anim_obj.update_spec(trans.duration_ms, curve)
        else:
            if anim_obj is not None:  # is_running=True implies anim_obj is not None
                # Running: re-target mid-flight without delay.
                anim_obj.update_spec(trans.duration_ms, curve)

        if anim_obj:
            if is_natural_target and isinstance(anim_obj, GenericPropertyAnimation):
                # target_raw was computed by _get_natural_size (layout-assigned natural width),
                # so use it directly rather than the stale anim_obj.natural_val.
                anim_obj.set_target(target_raw, clean_on_finish=True)
            else:
                anim_obj.set_target(target_raw)
            # Negative transition-delay: animation starts immediately but offset |delay| ms into
            # the timeline, as if it had already been running that long (CSS spec §transition-delay).
            # Only on fresh starts; re-targeting a running animation skips this.
            if not is_running and trans.delay_ms < 0:
                seek_ms = min(-trans.delay_ms, trans.duration_ms)
                anim_obj.anim.setCurrentTime(seek_ms)

            # Track class-change-initiated animations; re-evaluate on finish for deferred hover.
            if cause.is_class_driven and anim_obj.anim.state() == QAbstractAnimation.State.Running:
                ctx.class_anim_props.add(prop)
                gen = ctx.class_anim_gen
                wid = id(widget)

                # Disconnect the previous callback for this prop before connecting a new one.
                # Without this, rapid class changes accumulate closures on the finished signal.
                old_cb = ctx.class_anim_callbacks.pop(prop, None)
                if old_cb is not None:
                    try:
                        anim_obj.anim.finished.disconnect(old_cb)
                    except RuntimeError, TypeError:
                        pass

                def _on_class_anim_done(_w: QWidget = widget, _p: str = prop, _wid: int = wid, _gen: int = gen) -> None:
                    # Do NOT pop from class_anim_callbacks here — the next class change will
                    # disconnect and replace this.  Self-popping causes the next click to find
                    # no old callback to disconnect, re-connecting a second slot on the same signal.
                    c = self._contexts.get(_wid)
                    if c and _gen == c.class_anim_gen and _p in c.class_anim_props:
                        c.class_anim_props.discard(_p)
                        # Re-evaluate immediately so this prop can pick up deferred hover/focus
                        # changes as soon as its class animation unblocks it.  Other props that
                        # are still class-animating stay blocked via the class_anim_props check
                        # in _apply_prop_animation, so their animations are not disturbed.
                        self._evaluate_widget_state(_w, cause=EvaluationCause.CLASS_ANIMATION_FINISH)

                ctx.class_anim_callbacks[prop] = _on_class_anim_done
                anim_obj.anim.finished.connect(_on_class_anim_done)

            # Track :clicked forward-phase animations; deactivate :clicked when all finish.
            if (
                cause.is_clicked_driven
                and prop in ctx.clicked_anim_props
                and anim_obj.anim.state() == QAbstractAnimation.State.Running
            ):
                gen = ctx.clicked_anim_gen
                wid = id(widget)
                old_cb = ctx.clicked_anim_callbacks.pop(prop, None)
                if old_cb is not None:
                    try:
                        anim_obj.anim.finished.disconnect(old_cb)
                    except RuntimeError, TypeError:
                        pass

                def _on_clicked_anim_done(
                    _w: QWidget = widget, _p: str = prop, _wid: int = wid, _gen: int = gen
                ) -> None:
                    c = self._contexts.get(_wid)
                    if c and _gen == c.clicked_anim_gen and _p in c.clicked_anim_props:
                        c.clicked_anim_props.discard(_p)
                        if not c.clicked_anim_props:
                            self._deactivate_clicked(_w, _wid, _gen)

                ctx.clicked_anim_callbacks[prop] = _on_clicked_anim_done
                anim_obj.anim.finished.connect(_on_clicked_anim_done)
        return False

    def _resolve_target_raw(
        self,
        widget: QWidget,
        base_props: dict[str, str],
        target_props: dict[str, str],
        prop: str,
    ) -> tuple[str, bool]:
        """
        Resolve the CSS target value and whether it's a natural (unconstrained) target.

        A "natural target" means no explicit CSS value or 'auto' — the widget should
        return to its unconstrained layout size. For size props we animate toward sizeHint()
        then remove the constraint via clean_on_finish=True.

        Returns (target_raw, is_natural_target). target_raw is "" when there is no value
        and the property is non-animatable (caller should return early).
        """
        target_raw = target_props.get(prop) or base_props.get(prop)
        is_natural_target = prop in SIZE_PROPS and (not target_raw or target_raw == "auto")
        if target_raw == "auto":
            target_raw = self._get_natural_size(widget, base_props, prop)
        if not target_raw:
            if prop in SIZE_PROPS:
                target_raw = self._get_natural_size(widget, base_props, prop)
            elif "color" in prop:
                target_raw = "white" if prop == "color" else "transparent"
        return target_raw or "", is_natural_target

    def _refresh_natural_val(
        self,
        anim_obj: Animation,
        widget: QWidget,
        ctx: WidgetContext,
        base_props: dict[str, str],
        prop: str,
        is_natural_target: bool,
    ) -> None:
        """
        Update natural_val when returning to natural size after a class-change.

        natural_val may hold the old constrained size from a prior hover animation.
        Re-derive it from sizeHint() which reflects the unconstrained preferred size
        immediately after polish, before the layout has reflowed.
        Only applicable when pre_polish_size is set (i.e. a class-change is in progress).
        """
        if not (
            is_natural_target and isinstance(anim_obj, GenericPropertyAnimation) and ctx.pre_polish_size is not None
        ):
            return
        natural_str = self._get_natural_size(widget, base_props, prop)
        natural_num = parse_css_val(natural_str)
        if isinstance(natural_num, (int, float)):
            anim_obj.natural_val = float(natural_num)

    def _cleanup_orphans(
        self, _widget: QWidget, ctx: WidgetContext, all_animated_props: set[str], base_props: dict[str, str]
    ) -> bool:
        """
        Snap/stop animations for props no longer covered by any rule (e.g. a class was removed).

        Returns True if a batched style update is needed.
        """
        needs_update = False
        # Cancel pending delay timers for props that are no longer covered by any rule.
        for prop in list(ctx.pending_delays.keys()):
            if prop not in all_animated_props:
                t = ctx.pending_delays.pop(prop)
                t.stop()
                t.deleteLater()
        for prop, orphan in list(ctx.active_animations.items()):
            if prop in all_animated_props:
                continue
            ctx.class_anim_props.discard(prop)
            old_cb = ctx.class_anim_callbacks.pop(prop, None)
            if old_cb is not None:
                try:
                    orphan.anim.finished.disconnect(old_cb)
                except RuntimeError, TypeError:
                    pass
            snap_target = base_props.get(prop)
            if snap_target == "auto":
                snap_target = None
            is_natural_snap = not snap_target and prop in SIZE_PROPS
            if is_natural_snap:
                snap_target = get_preferred_size_fallback(orphan.widget, base_props, prop)
            if snap_target:
                if is_natural_snap and isinstance(orphan, GenericPropertyAnimation):
                    orphan.snap_to_natural()
                else:
                    orphan.snap_to(snap_target)
            else:
                orphan.anim.stop()
                if isinstance(orphan, BoxShadowHandle):
                    apply_shadow_to_widget(orphan.widget, None, self.effect_priority)
                elif isinstance(orphan, OpacityAnimation):
                    try:
                        orphan.widget.setGraphicsEffect(None)
                    except RuntimeError:
                        pass
            if not isinstance(orphan, (OpacityAnimation, BoxShadowHandle)):
                needs_update = True
            del ctx.active_animations[prop]
            orphan.deleteLater()
        # Also evict stale snapped props: entries in css_anim_props that are no longer
        # in any rule and have no backing animation object (e.g. a prop was removed from
        # CSS and the widget was re-evaluated via Polish with old rules during hot-reload).
        stale_snapped = {
            p for p in ctx.css_anim_props if p not in all_animated_props and p not in ctx.active_animations
        }
        if stale_snapped:
            for p in stale_snapped:
                del ctx.css_anim_props[p]
            needs_update = True
        return needs_update

    # -------------------------------------------------------------------------
    # Rule hot-reload
    # -------------------------------------------------------------------------

    def reload_rules(self, rules: list[StyleRule]) -> None:
        """
        Hot-reload CSS rules: stop all animations, clear inline styles, update rules.

        Call this before app.setStyleSheet() so that the Polish events triggered by
        the stylesheet change already see the new rules.
        """
        animated_widgets: set[QWidget] = set()
        # Widgets with inline animations (ColorAnimation / GenericPropertyAnimation) write to
        # css_anim_props / setStyleSheet, so widget.setStyleSheet("") triggers a Polish event —
        # they must stay on the Polish path and must NOT be touched by the deferred callback.
        inline_widget_ids: set[int] = set()
        for _wid, ctx in list(self._contexts.items()):
            if not ctx.active_animations:
                continue
            # Grab a live widget ref from the first animation object.
            sample = next(iter(ctx.active_animations.values()))
            try:
                sample.widget.objectName()
                animated_widgets.add(sample.widget)
                if any(not isinstance(a, (BoxShadowHandle, OpacityAnimation)) for a in ctx.active_animations.values()):
                    inline_widget_ids.add(_wid)
            except RuntimeError:
                pass
        for _wid, ctx in list(self._contexts.items()):
            for timer in ctx.pending_delays.values():
                try:
                    timer.stop()
                    timer.deleteLater()
                except RuntimeError:
                    pass
            ctx.pending_delays.clear()
            for prop, cb in list(ctx.clicked_anim_callbacks.items()):
                anim_obj = ctx.active_animations.get(prop)
                if anim_obj is not None:
                    try:
                        anim_obj.anim.finished.disconnect(cb)
                    except RuntimeError, TypeError:
                        pass
            ctx.clicked_anim_callbacks.clear()
            ctx.clicked_anim_props.clear()
            ctx.active_pseudos.discard(":clicked")
            for anim_obj in ctx.active_animations.values():
                try:
                    anim_obj.anim.stop()
                    anim_obj.deleteLater()
                except RuntimeError:
                    pass
            ctx.active_animations.clear()

        # Update rules *before* clearing widget state so any Polish events triggered by
        # the setStyleSheet("") calls below already see the new rules and do not re-snap
        # properties that were just removed from CSS.
        self.rules = rules
        self._build_quick_filters()
        self._rule_cache.clear()

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

        # Clear stale inline styles from snap-only widgets (those with css_anim_props set but no
        # active Animation object — not included in animated_widgets above).  If new rules remove
        # transitions for such a widget, _should_evaluate returns False and it is never
        # re-evaluated, leaving the old inline style permanently overriding the app stylesheet.
        app_inst = QApplication.instance()
        if isinstance(app_inst, QApplication):
            for w in app_inst.allWidgets():
                if id(w) in animated_widget_ids:
                    continue
                try:
                    ctx = self._contexts.get(id(w))
                    if ctx is not None and ctx.css_anim_props:
                        ctx.css_anim_props.clear()
                        w.setStyleSheet("")
                except RuntimeError:
                    pass

        # Effect-only widgets (box-shadow / opacity with no inline animation) have no inline
        # stylesheet, so widget.setStyleSheet("") above was a no-op for them. If the cleaned
        # QSS is unchanged Qt won't send a Polish event, and the engine would never re-evaluate
        # them. Defer a targeted pass.
        effect_only_widgets = {w for w in animated_widgets if id(w) not in inline_widget_ids}
        prev_animated_ids = {id(w) for w in animated_widgets}

        QTimer.singleShot(0, lambda: self._reeval_effect_widgets_deferred(effect_only_widgets, prev_animated_ids))

    def _reeval_effect_widgets_deferred(self, effect_only_widgets: set[QWidget], prev_animated_ids: set[int]) -> None:
        """Re-evaluate effect-only widgets after a hot-reload stylesheet change."""
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
        if not self._has_effect_rules:
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

    # -------------------------------------------------------------------------
    # Animation helpers
    # -------------------------------------------------------------------------

    def _get_natural_size(self, widget: QWidget, base_props: dict[str, str], prop: str) -> str:
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
        ctx = self._ctx(widget)
        axis_props = {"width", "min-width", "max-width"} if "width" in prop else {"height", "min-height", "max-height"}
        constrained = {k for k in axis_props if k in ctx.css_anim_props}
        if not constrained:
            return get_preferred_size_fallback(widget, base_props, prop)
        stripped = {k: v for k, v in ctx.css_anim_props.items() if k not in constrained}
        parent = widget.parentWidget()
        parent_layout = parent.layout() if parent is not None else None
        ctx.internal_write_depth += 1
        ctx.internal_write_reason = InternalWriteReason.MEASURE
        try:
            widget.setStyleSheet(scoped_anim_style(widget, stripped))
            # setStyleSheet() calls style.polish() synchronously, which updates
            # widget.maximumWidth/minimumWidth.  activate() then assigns the natural size.
            if parent_layout is not None:
                parent_layout.activate()
                raw_px = widget.width() if "width" in prop else widget.height()
                actual = content_box_px(widget, base_props, prop, raw_px)
                result = f"{actual}px" if actual > 0 else get_preferred_size_fallback(widget, base_props, prop)
            else:
                result = get_preferred_size_fallback(widget, base_props, prop)
        finally:
            widget.setStyleSheet(scoped_anim_style(widget, ctx.css_anim_props))
            # Restore the constrained geometry so there is no flash before animation starts.
            if parent_layout is not None:
                parent_layout.activate()
            ctx.internal_write_depth -= 1
            if ctx.internal_write_depth == 0:
                ctx.internal_write_reason = None
        return result

    def _is_animatable(self, prop: str) -> bool:
        """Return True if the engine knows how to animate this CSS property."""
        return "color" in prop or prop in EFFECT_PROPS or prop in SUPPORTED_NUMERIC_PROPS

    def _register_animation(self, widget: QWidget, ctx: WidgetContext, prop: str, anim_obj: Animation) -> None:
        """Register an animation object and ensure widget destroyed cleanup is wired."""
        ctx.active_animations[prop] = anim_obj
        self._connect_destroyed(widget)

    def _schedule_delayed_animation(self, widget: QWidget, ctx: WidgetContext, prop: str, delay_ms: int) -> None:
        """Schedule prop's animation to start after delay_ms, wiring up the destroyed signal."""
        # Ensure the destroyed signal is connected so _on_widget_destroyed can cancel this timer.
        self._connect_destroyed(widget)
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
                pass  # C++ widget destroyed between timer start and fire

        timer.timeout.connect(_fire)
        ctx.pending_delays[prop] = timer
        timer.start(delay_ms)

    def _fire_delayed_prop(self, widget: QWidget, prop: str) -> None:
        """Re-evaluate a single property after its transition-delay has elapsed."""
        if not self._should_evaluate(widget):
            return
        ctx = self._ctx(widget)
        base_props, target_props, target_transitions, all_animated_props = self._collect_rule_state(widget, ctx)
        if prop not in all_animated_props:
            return
        needs_update = self._apply_prop_animation(
            widget, ctx, prop, base_props, target_props, target_transitions, EvaluationCause.DELAY_FIRE
        )
        if needs_update:
            widget.setStyleSheet(scoped_anim_style(widget, ctx.css_anim_props))

    def _snap_prop_or_effect(
        self,
        widget: QWidget,
        ctx: WidgetContext,
        prop: str,
        anim_obj: Animation | None,
        target_raw: str,
        is_natural_target: bool,
    ) -> bool:
        """Snap a property to its target value instantly. Returns True if a batched style update is needed."""
        if anim_obj:
            if is_natural_target and isinstance(anim_obj, GenericPropertyAnimation):
                anim_obj.snap_to_natural()
            else:
                anim_obj.snap_to(target_raw)
            return not isinstance(anim_obj, (OpacityAnimation, BoxShadowHandle))
        if prop in EFFECT_PROPS:
            new_anim = self._create_animation_obj(widget, prop, target_raw, 0, QEasingCurve.Type.Linear)
            if new_anim:
                self._register_animation(widget, ctx, prop, new_anim)
            return False
        if is_natural_target:
            # Snap to natural = remove the inline constraint so Qt lays out at its preferred size.
            if prop in ctx.css_anim_props:
                del ctx.css_anim_props[prop]
            return True
        ctx.css_anim_props[prop] = target_raw
        return True

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

    def _resolve_easing_curve(self, easing: str) -> QEasingCurve:
        """Parse a CSS timing-function string into a QEasingCurve."""
        if m := _CUBIC_BEZIER_RE.match(easing):
            return make_cubic_bezier_curve(float(m[1]), float(m[2]), float(m[3]), float(m[4]))
        if m := _STEPS_RE.match(easing):
            return make_steps_curve(int(m[1]), m[2] or "end")
        if easing == "step-start":
            return make_steps_curve(1, "start")
        if easing == "step-end":
            return make_steps_curve(1, "end")
        return QEasingCurve(EASING_MAP.get(easing, QEasingCurve.Type.InOutQuad))

    def _resolve_current_raw(
        self, widget: QWidget, ctx: WidgetContext, prop: str, base_props: dict[str, str], base_raw: str
    ) -> str:
        """Resolve the CSS value to use as the animation start point.

        For size props, accounts for border/padding/margin to derive the content-area
        pixel value from the widget's actual rendered geometry (or pre-polish snapshot).
        Falls back to base_raw if no better source is available.
        """
        current_raw = ctx.css_anim_props.get(prop)
        if current_raw is None:
            if prop in SIZE_PROPS:
                pre_polish = ctx.pre_polish_size
                if "width" in prop:
                    raw_px = pre_polish[0] if pre_polish is not None else widget.width()
                else:
                    raw_px = pre_polish[1] if pre_polish is not None else widget.height()
                actual = content_box_px(widget, base_props, prop, raw_px)
                current_raw = f"{actual}px" if actual > 0 else base_raw
            else:
                current_raw = base_raw
        return current_raw

    def _create_animation_obj(
        self,
        widget: QWidget,
        prop: str,
        initial_raw: str,
        duration_ms: int,
        curve: QEasingCurve | QEasingCurve.Type,
    ) -> Animation | None:
        """Instantiate the correct Animation subclass for a CSS property."""
        ctx = self._ctx(widget)
        if "color" in prop:
            return ColorAnimation(widget, prop, initial_raw, duration_ms, curve, self, ctx=ctx)
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
                return GenericPropertyAnimation(widget, prop, start_val, duration_ms, curve, self, unit=unit, ctx=ctx)
        return None
