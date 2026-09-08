import logging
import os
from typing import TYPE_CHECKING

from qt_css_engine.animation.delay import DelayScheduler
from qt_css_engine.engine.cascade import CascadeEvaluator
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.evaluator import WidgetEvaluator
from qt_css_engine.engine.event_router import EventRouter
from qt_css_engine.engine.handlers import class_change as class_change_handler
from qt_css_engine.engine.handlers import clicked as clicked_handler
from qt_css_engine.engine.handlers import lifecycle as lifecycle_handler
from qt_css_engine.engine.handlers import parent_change as parent_change_handler
from qt_css_engine.engine.handlers import reload as reload_handler
from qt_css_engine.engine.handlers import window as window_handler
from qt_css_engine.engine.handlers.polish import PolishQueue
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEvent, QObject, Qt, QTimer
from qt_css_engine.qt_compat.QtWidgets import QAbstractButton, QWidget
from qt_css_engine.state.pseudo import PseudoMachine
from qt_css_engine.state.store import WidgetStore
from qt_css_engine.state.suppress import is_suppressed
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.writer import StyleWriter
from qt_css_engine.utils.qt_helpers import safe_disconnect

if TYPE_CHECKING:
    from qt_css_engine.css.model import StyleRule


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
        """
        super().__init__(parent)
        self.matcher = RuleMatcher(rules)
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
        self.active_rule_widgets: dict[int, QWidget] = {}
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

        # Deferred Polish burst evaluation. PolishQueue owns pending/queue/force_ids.
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
            # Engine wires its own comprehensive _on_widget_destroyed (stops
            # animations); the store stays pure storage with no signal wiring.
            ctx = self.store.get_or_create(widget)
            widget.destroyed.connect(lambda: self._on_widget_destroyed(widget))
        return ctx

    # -------------------------------------------------------------------------
    # Event filtering and pseudo-state tracking
    # -------------------------------------------------------------------------

    # Qt stubs name these params a0/a1; watched/event kept for readability (external-stub ignore).
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        """Intercept widget events to track pseudo-states and trigger CSS transitions."""
        event_type = event.type()
        if not EventRouter.is_relevant(event_type, watched) or not isinstance(watched, QWidget):
            return False
        EventRouter.dispatch(self, watched, event, event_type)
        return False

    def on_polish(self, widget: QWidget) -> None:
        """Handle Polish events — evaluate initial widget state on first polish."""
        ctx = self.store.contexts.get(id(widget))
        # Ignore Polish events triggered by our own internal style writes.
        if is_suppressed(ctx):
            return
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
        """Refresh static border-radius clamps after layout assigns a new widget size."""
        if not self.matcher.index.flags.has_border_radius:
            return
        ctx = self.store.contexts.get(id(widget))
        if is_suppressed(ctx):
            return
        if ctx is not None and self._has_running_animation(ctx):
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

    def on_class_change(self, widget: QWidget) -> None:
        """Handle class property change — snapshot size, unpolish/polish, and kick off animations."""
        class_change_handler.handle_class_change(self, widget)

    def on_attr_change(self, widget: QWidget) -> None:
        """Handle tracked `[attr=value]` dynamic-property change.

        Same path as a class change: the structural match is unchanged, but the
        cascade target (and Qt's native non-animated props) must be re-resolved.
        Descendant caches are invalidated too, so ancestor-attr selectors refresh
        on their next evaluation.
        """
        class_change_handler.handle_class_change(self, widget)

    def on_parent_change(self, widget: QWidget) -> None:
        """Handle reparenting; ancestor-dependent selectors may now match differently."""
        parent_change_handler.handle_parent_change(self, widget)

    def on_window_activate(self, widget: QWidget) -> None:
        """Set :active on children that have :active rules when the window gains focus."""
        window_handler.handle_window_activate(self, widget)

    def on_window_deactivate(self, widget: QWidget, *, clear_active: bool = True) -> None:
        """Clear stuck :hover/:pressed/:active states when the window loses focus."""
        window_handler.handle_window_deactivate(self, widget, clear_active=clear_active)

    def prepare_clicked(self, widget: QWidget, ctx: WidgetState, updated: set[str]) -> EvaluationCause:
        return clicked_handler.prepare_clicked(self, widget, ctx, updated)

    def finish_clicked_activation(self, widget: QWidget, ctx: WidgetState) -> None:
        clicked_handler.finish_clicked_activation(self, widget, ctx)

    def deactivate_clicked(self, widget: QWidget, wid: int, gen: int) -> None:
        clicked_handler.deactivate_clicked(self, widget, wid, gen)

    def ensure_wa_hover(self, widget: QWidget) -> None:
        """Set WA_Hover on widget if it matches any rule with a :hover pseudo-class."""
        if widget.testAttribute(Qt.WidgetAttribute.WA_Hover):
            return  # already set
        if not self.should_evaluate(widget):
            return
        if any(":hover" in rule.pseudo_set for rule in self.matcher.matching_rules(widget)):
            widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def seed_active_pseudo(self, widget: QWidget) -> None:
        """Add :active to the widget's pseudo set at Polish time if its window is currently active."""
        if not any(":active" in r.pseudo_set for r in self.matcher.matching_rules(widget)):
            return
        ctx = self.get_context(widget)
        self.active_rule_widgets[id(widget)] = widget
        if not widget.isActiveWindow():
            return
        if not self.should_evaluate(widget):
            return
        ctx.active_pseudos.add(":active")

    def connect_checkable(self, widget: QWidget) -> None:
        """Connect to toggled signal for checkable buttons and sync initial :checked state."""
        if not isinstance(widget, QAbstractButton):
            return
        wid = id(widget)
        if wid in self.connected_checkable_ids:
            return
        self.connected_checkable_ids.add(wid)
        if widget.isChecked():
            self.get_context(widget).active_pseudos.add(":checked")

        def _on_toggle(checked: bool, w: QWidget = widget) -> None:
            self.on_checked_changed(w, checked)

        widget.toggled.connect(_on_toggle)

    def on_checked_changed(self, widget: QWidget, checked: bool) -> None:
        """Sync :checked pseudo-state and re-evaluate transitions on button toggle."""
        ctx = self.get_context(widget)
        if checked:
            ctx.active_pseudos.add(":checked")
        else:
            ctx.active_pseudos.discard(":checked")
        self.evaluate_widget_state(widget, cause=EvaluationCause.PSEUDO_STATE)

    # -------------------------------------------------------------------------
    # Widget lifecycle tracking
    # -------------------------------------------------------------------------

    def _on_widget_destroyed(self, widget: QWidget) -> None:
        lifecycle_handler.on_widget_destroyed(self, widget)

    # -------------------------------------------------------------------------
    # State evaluation — delegates to WidgetEvaluator (explicit pipeline object)
    # -------------------------------------------------------------------------

    def evaluate_widget_state(self, widget: QWidget, cause: EvaluationCause = EvaluationCause.DIRECT) -> None:
        """Evaluate all animated CSS properties for widget and start, update, or snap animations."""
        self.evaluator.evaluate(widget, cause)

    # -------------------------------------------------------------------------
    # Rule hot-reload — delegates to reload handler
    # -------------------------------------------------------------------------

    def reload_rules(self, rules: list[StyleRule]) -> None:
        reload_handler.reload_rules(self, rules)
