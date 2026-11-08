import logging
import os
import weakref
from typing import TYPE_CHECKING

from qt_css_engine.animation.delay import DelayScheduler
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.constants import BORDER_RADIUS_PROPS, ENGINE_EVENT_TYPES
from qt_css_engine.engine.cascade import CascadeEvaluator
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.evaluator import WidgetEvaluator
from qt_css_engine.engine.event_router import EventRouter
from qt_css_engine.engine.handlers import lifecycle as lifecycle_handler
from qt_css_engine.engine.handlers import reload as reload_handler
from qt_css_engine.engine.handlers.polish import PolishQueue
from qt_css_engine.matching.local import WidgetStyleMatcher
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEvent, QObject, Qt, QTimer
from qt_css_engine.qt_compat.QtWidgets import QAbstractButton, QWidget
from qt_css_engine.state.pseudo import PseudoMachine
from qt_css_engine.state.store import WidgetStore
from qt_css_engine.state.suppress import is_suppressed
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.cursor import apply_cursor
from qt_css_engine.style.writer import StyleWriter
from qt_css_engine.utils.qt_helpers import safe_disconnect, weak_widget_callback

if TYPE_CHECKING:
    from qt_css_engine.css.model import StyleRule


event_logger = logging.getLogger("qt_css_engine.event")


class TransitionEngine(QObject):
    """
    Core CSS transition engine for PyQt6/PySide6.

    Installed as a global event filter on QApplication or individual QWidget. Intercepts hover, mouse,
    and focus events to track widget pseudo-states, evaluates the CSS cascade,
    and drives smooth property animations via Qt's animation framework.

    Event handlers only update widget context and trigger evaluation. Evaluation then
    follows one path: collect rule state, resolve each property, animate or snap it,
    clean up orphaned animations, and flush the resulting inline style once.
    """

    # Which effect wins the widget's single graphics-effect slot when both opacity and
    # box-shadow are declared on the same widget. The loser becomes a silent no-op.
    # "box-shadow" → QGraphicsDropShadowEffect takes priority over opacity.
    # "opacity"    → QGraphicsOpacityEffect takes priority over box-shadow.
    # Prefer passing `effect_priority` to __init__.
    effect_priority: str = "opacity"

    def __init__(
        self,
        rules: list[StyleRule],
        parent: QObject | None = None,
        startup_delay_ms: int = 100,
        effect_priority: str = "opacity",
        *,
        collect_widget_styles: bool = False,
    ) -> None:
        """
        Initialise the engine with a parsed rule set.

        startup_delay_ms: animations are suppressed for this many milliseconds after
        construction so that initial layout polish events don't trigger spurious transitions.
        Set to 0 to enable immediately (synchronous — useful in tests).
        Assigning `animations_enabled` manually cancels the pending timer, so
        explicit intent always wins over the scheduled auto-enable.

        effect_priority: which effect wins the single QGraphicsEffect slot — "opacity"
        or "box-shadow". When both are declared the loser is silently dropped.

        collect_widget_styles: capture widget-local QSS at first Polish, caching
        repeated text and respecting widget/ancestor stylesheet precedence.
        Install the filter before polishing widgets. Later stylesheet replacements
        are not observed; this option does not install a StyleChange watcher.
        Explicit set_widget_stylesheet() registration works independently of this option.
        """
        super().__init__(parent)
        self.matcher = WidgetStyleMatcher(rules) if collect_widget_styles else RuleMatcher(rules)
        self.cascade = CascadeEvaluator(self.matcher, PseudoMachine.priority)
        self.effect_priority = effect_priority

        self._animations_enabled = True
        self._startup_timer: QTimer | None = None
        if startup_delay_ms > 0:
            self._animations_enabled = False
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_startup_done)
            self._startup_timer = timer
            timer.start(startup_delay_ms)

        # Single source of truth for per-widget state.
        self.store = WidgetStore()
        # Widgets that have at least one :active rule — populated at Polish time for O(1) activate/deactivate.
        self.active_rule_widgets: weakref.WeakValueDictionary[int, QWidget] = weakref.WeakValueDictionary()
        # One cleanup callback for both Qt destruction and Python wrapper collection.
        self.widget_finalizers: dict[int, weakref.finalize[[], QWidget]] = {}
        # Checkable widget IDs already connected to toggled signal.
        self.connected_checkable_ids: set[int] = set()

        # Enable event logging if the CSS_ENGINE_EVENT_LOGGING env var is set.
        if os.environ.get("CSS_ENGINE_EVENT_LOGGING", "").lower() not in ("1", "true", "yes"):
            event_logger.disabled = True
        # When True, middle/right clicks are ignored entirely (no :pressed/:clicked animations).
        # Controlled by CSS_ENGINE_LEFT_CLICK_ONLY env var.
        self.left_click_only: bool = os.environ.get("CSS_ENGINE_LEFT_CLICK_ONLY", "").lower() in ("1", "true", "yes")
        # Timestamp of the last non-left mouse press event claimed by a widget with matching rules.
        # Prevents :pressed from propagating to ancestor widgets on middle/right click.
        self.claimed_mouse_event_ts: int = -1

        # Deferred Polish burst evaluation. PolishQueue owns pending/queue/forced_widgets.
        self.polish = PolishQueue()
        self.delays = DelayScheduler(self)
        # Scoped inline-style flush coalescing + dedup. Bound to the store lookup so
        # deferred singleShot(0) flushes resolve the live context (or drop if destroyed).
        self.writer = StyleWriter(get_ctx=self.store.contexts.get)
        # Explicit evaluation pipeline — collect -> resolve -> animate/snap -> cleanup -> flush.
        self.evaluator = WidgetEvaluator(engine=self)

    def should_evaluate(self, widget: QWidget) -> bool:
        return self.matcher.should_evaluate(widget, self.store.contexts.get(id(widget)))

    @property
    def animations_enabled(self) -> bool:
        """Whether transitions animate (False → snap to target).
        Writing this property cancels any pending startup auto-enable.
        """
        return self._animations_enabled

    @animations_enabled.setter
    def animations_enabled(self, value: bool) -> None:
        self._animations_enabled = value
        self._cancel_startup_timer()

    def _cancel_startup_timer(self) -> None:
        """Stop and release the pending startup timer, if any."""
        timer = self._startup_timer
        self._startup_timer = None
        if timer is not None:
            try:
                timer.stop()
                safe_disconnect(timer.timeout)
                timer.deleteLater()
            except RuntimeError:
                pass

    def _on_startup_done(self) -> None:
        """Enable animations after the startup delay has elapsed."""
        timer = self._startup_timer
        self._startup_timer = None
        if timer is not None:
            timer.deleteLater()
        self._animations_enabled = True

    def get_context(self, widget: QWidget) -> WidgetState:
        """Get or create the context for a widget via the store."""
        ctx = self.store.get(widget)
        if ctx is None:
            # Register cleanup for destruction and collection; the store stays pure storage.
            ctx = self.store.get_or_create(widget)
            lifecycle_handler.track_widget_lifetime(self, widget)
        if isinstance(self.matcher, WidgetStyleMatcher):
            ctx.style.local_sheet = self.matcher.sources.get(id(widget))
        return ctx

    def discover_widget_styles(self, widget: QWidget) -> None:
        if not isinstance(self.matcher, WidgetStyleMatcher):
            return
        for owner, affected in self.matcher.discover(widget):
            source = self.matcher.sources[id(owner)]
            lifecycle_handler.track_widget_lifetime(self, owner)
            ctx = self.store.get(owner)
            if ctx is None and source.needs_cleaning:
                ctx = self.store.get_or_create(owner)
            if ctx is not None:
                ctx.style.local_sheet = source
            if source.needs_cleaning:
                self.queue_polish_evaluation(owner)
            for target in affected:
                if self.should_evaluate(target):
                    self.queue_polish_evaluation(target, force=True)

    def prepare_widget_style(self, widget: QWidget) -> None:
        """Clean newly discovered engine declarations once, outside the Polish burst."""
        ctx = self.store.get(widget)
        if ctx is not None and ctx.style.local_sheet is not None:
            if ctx.style.local_sheet.needs_cleaning and ctx.applied_style is None:
                self.writer.flush_now(widget, ctx)

    def set_widget_stylesheet(self, widget: QWidget, stylesheet: str) -> None:
        """Cache full local CSS and send only cleaned QSS to Qt.

        Works with or without collect_widget_styles. Without automatic discovery,
        only explicitly registered local sheets enter the engine's scoped cascade.
        Use this for engine extensions: Qt can parse raw QSS before any Polish event.
        Explicit replacements invalidate the widget's subtree and discard old
        animations; evaluation remains deferred through the Polish queue.
        """
        if not isinstance(self.matcher, WidgetStyleMatcher):
            # Activate local-source support only when requested. The default path
            # keeps its existing matcher and never reads widgets' authored QSS.
            self.matcher = WidgetStyleMatcher(self.matcher.rules, auto_discovery=False)
            self.cascade = CascadeEvaluator(self.matcher, PseudoMachine.priority)
        source, affected = self.matcher.set_source(widget, stylesheet)
        lifecycle_handler.track_widget_lifetime(self, widget)
        for target in (widget, *affected):
            existing = self.store.get(target)
            if existing is None:
                continue
            reload_handler.reset_context_for_reload(self, existing)
            existing.css_anim_props.clear()
            existing.style_box_props.clear()
            existing.style.local_overlay_selectors = None
            existing.style_flush_pending = False
            apply_cursor(target, existing, {})
        ctx = self.store.get(widget)
        if ctx is None and source is not None and source.needs_cleaning:
            ctx = self.store.get_or_create(widget)
        qss = source.qss if source is not None else ""
        if ctx is not None:
            ctx.style.local_sheet = source
            ctx.applied_style = qss
        widget.setStyleSheet(qss)
        for target in affected:
            existing = self.store.get(target)
            if existing is not None and existing.applied_style is not None:
                # Descendants may no longer pass the evaluation gate after a
                # source is removed. Release their old overlays now, retaining
                # each descendant's independently authored local sheet.
                own_source = existing.style.local_sheet
                base_qss = own_source.qss if own_source is not None else ""
                existing.applied_style = base_qss
                if target.styleSheet() != base_qss:
                    target.setStyleSheet(base_qss)
        for target in (widget, *affected):
            if target.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
                self.queue_polish_evaluation(target, force=True)

    # -------------------------------------------------------------------------
    # Event filtering and pseudo-state tracking
    # -------------------------------------------------------------------------

    # Qt stubs name these params a0/a1; watched/event kept for readability (external-stub ignore).
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        """Intercept widget events to track pseudo-states and trigger CSS transitions."""
        event_type = event.type()
        if event_type not in ENGINE_EVENT_TYPES or not isinstance(watched, QWidget):
            return False
        EventRouter.dispatch(self, watched, event, event_type)
        return False

    def on_polish(self, widget: QWidget) -> None:
        """Handle Polish events — evaluate initial widget state on first polish."""
        ctx = self.store.contexts.get(id(widget))
        # Ignore Polish events triggered by our own internal style writes.
        if is_suppressed(ctx):
            return
        if isinstance(self.matcher, WidgetStyleMatcher):
            self.discover_widget_styles(widget)
            ctx = self.store.contexts.get(id(widget))
        # Wire up the toggled signal for checkable widgets (idempotent).
        self.connect_checkable(widget)
        # Skip widgets that no animated/effect rule could ever touch.
        if not self.should_evaluate(widget):
            return
        if ctx is not None and ctx.active_animations:
            return
        # Defer all expensive work to after the burst. Qt fires Polish for every child widget synchronously
        self.queue_polish_evaluation(widget)

    def on_resize(self, widget: QWidget) -> None:
        """Refresh radius geometry without snapping unrelated running transitions."""
        if not self.matcher.index.flags.has_border_radius:
            return
        ctx = self.store.contexts.get(id(widget))
        if is_suppressed(ctx):
            return
        if ctx is not None and self._has_running_animation(ctx):
            # Layout may change an unconstrained axis while padding/size animates.
            # Refresh only geometry-dependent values; a Polish evaluation would
            # snap every running transition to its destination.
            if ctx.css_anim_props.keys() & BORDER_RADIUS_PROPS:
                for prop, animation in ctx.active_animations.items():
                    if prop in BORDER_RADIUS_PROPS and isinstance(animation, GenericPropertyAnimation):
                        animation.refresh_radius_geometry()
                self.writer.flush_now(widget, ctx)
            return
        if not self.should_evaluate(widget):
            return
        if not any(rule.has_border_radius_props for rule in self.matcher.matching_rules(widget)):
            return
        event_logger.debug("On resize event: %s", widget)
        self.queue_polish_evaluation(widget, force=True)

    @staticmethod
    def _has_running_animation(ctx: WidgetState) -> bool:
        """Return True while any registered animation is actively transitioning."""
        return any(
            anim_obj.anim.state() == QAbstractAnimation.State.Running for anim_obj in ctx.active_animations.values()
        )

    def queue_polish_evaluation(self, widget: QWidget, *, force: bool = False) -> None:
        """Queue a widget for a deferred polish-style state evaluation."""
        self.polish.enqueue(widget, force=force, schedule_flush=lambda: QTimer.singleShot(0, self._flush_polish_queue))

    def _flush_polish_queue(self) -> None:
        """Drain the deferred Polish evaluation queue after a burst completes."""
        self.polish.flush(self)

    def ensure_wa_hover(self, widget: QWidget, *, rules: list[StyleRule] | None = None) -> None:
        """Set WA_Hover on widget if it matches any rule with a :hover pseudo-class.

        *rules* must equal matching_rules(widget); gate and lookup are skipped.
        """
        if widget.testAttribute(Qt.WidgetAttribute.WA_Hover):
            return  # already set
        if not self.matcher.index.flags.has_hover:
            return
        if rules is None and not self.should_evaluate(widget):
            return
        matched = rules if rules is not None else self.matcher.matching_rules(widget)
        if any(":hover" in rule.pseudo_set for rule in matched):
            widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def seed_active_pseudo(self, widget: QWidget, *, rules: list[StyleRule] | None = None) -> None:
        """Add :active to the widget's pseudo set at Polish time if its window is currently active.

        *rules* must equal matching_rules(widget); gate and lookup are skipped.
        """
        if not self.matcher.index.flags.has_active:
            return
        matched = rules if rules is not None else self.matcher.matching_rules(widget)
        if not any(":active" in r.pseudo_set for r in matched):
            return
        ctx = self.get_context(widget)
        self.active_rule_widgets[id(widget)] = widget
        if not widget.isActiveWindow():
            return
        if rules is None and not self.should_evaluate(widget):
            return
        ctx.active_pseudos.add(":active")

    def connect_checkable(self, widget: QWidget) -> None:
        """Connect to toggled signal for checkable buttons and sync initial :checked state."""
        if not isinstance(widget, QAbstractButton):
            return
        wid = id(widget)
        if wid in self.connected_checkable_ids:
            return
        lifecycle_handler.track_widget_lifetime(self, widget)
        self.connected_checkable_ids.add(wid)
        if widget.isChecked():
            self.get_context(widget).active_pseudos.add(":checked")

        widget.toggled.connect(weak_widget_callback(widget, self.on_checked_changed))

    def on_checked_changed(self, widget: QWidget, checked: bool) -> None:
        """Sync :checked pseudo-state and re-evaluate transitions on button toggle."""
        ctx = self.get_context(widget)
        if checked:
            ctx.active_pseudos.add(":checked")
        else:
            ctx.active_pseudos.discard(":checked")
        self.evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    # -------------------------------------------------------------------------
    # State evaluation — delegates to WidgetEvaluator (explicit pipeline object)
    # -------------------------------------------------------------------------

    def evaluate_widget_state(
        self, widget: QWidget, cause: EvaluationCause = EvaluationCause.DIRECT, *, rules: list[StyleRule] | None = None
    ) -> None:
        """Evaluate all animated CSS properties for widget and start, update, or snap animations."""
        self.evaluator.evaluate(widget, cause, rules=rules)

    # -------------------------------------------------------------------------
    # Rule hot-reload — delegates to reload handler
    # -------------------------------------------------------------------------

    def reload_rules(self, rules: list[StyleRule]) -> None:
        reload_handler.reload_rules(self, rules)
