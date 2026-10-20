"""Non-owning effect-slot dependencies for CSS effect targets.

Discover a target's branch when it first needs an effect or its hierarchy changes.
Observe events only on the widgets in those cached branches.
Before repainting, inspect only those cached slots, once per window. Unchanged
slots need no relationship checks, and animation ticks use the cached result.
"""

import weakref
from dataclasses import dataclass, field

from qt_css_engine.qt_compat import is_qobject_alive
from qt_css_engine.qt_compat.QtCore import QEvent, QObject
from qt_css_engine.qt_compat.QtWidgets import QApplication, QGraphicsEffect, QWidget
from qt_css_engine.utils.qt_helpers import safe_disconnect


@dataclass
class _Scope:
    """Ancestors and descendants in the target's window, including the target."""

    nodes: tuple[weakref.ref[QWidget], ...] = ()
    foreign_count: int = 0


@dataclass
class _Slot:
    targets: weakref.WeakSet[QWidget] = field(default_factory=weakref.WeakSet)
    # Only these targets need rediscovery when this node gains or loses children.
    subtree_targets: weakref.WeakSet[QWidget] = field(default_factory=weakref.WeakSet)
    window_ref: weakref.ref[QWidget] | None = None
    effect_ref: weakref.ref[QGraphicsEffect] | None = None
    foreign: bool = False
    finalizer: weakref.finalize[[], QWidget] | None = None


_policy: _EffectPolicy | None = None
_POLICY_EVENTS = frozenset(
    {
        QEvent.Type.UpdateRequest,
        QEvent.Type.Show,
        QEvent.Type.ChildAdded,
        QEvent.Type.ChildRemoved,
        QEvent.Type.ParentChange,
    }
)


def owns_effect(widget: QWidget, effect: QGraphicsEffect) -> bool:
    effect_ref: weakref.ref[QGraphicsEffect] | None = getattr(widget, "_css_engine_effect_ref", None)
    return effect_ref is not None and effect_ref() is effect


def install_engine_effect(widget: QWidget, effect: QGraphicsEffect) -> None:
    """Parent and install a new effect without invalidating widget relationships."""
    if _policy is not None:
        _policy.internal_write_depth += 1
    try:
        effect.setParent(widget)
        widget.setGraphicsEffect(effect)
        setattr(widget, "_css_engine_effect_ref", weakref.ref(effect))
    finally:
        if _policy is not None:
            _policy.internal_write_depth -= 1


def clear_engine_effect(widget: QWidget) -> None:
    """Remove only the exact effect installed by the engine."""
    effect = widget.graphicsEffect()
    if effect is None or not owns_effect(widget, effect):
        return
    if _policy is not None:
        _policy.internal_write_depth += 1
    try:
        setattr(widget, "_css_engine_effect_ref", None)
        widget.setGraphicsEffect(None)
    finally:
        if _policy is not None:
            _policy.internal_write_depth -= 1
    if _policy is not None:
        _policy.release_unblocked(widget)


def release_effect_scope(widget: QWidget) -> None:
    """Stop observing a widget whose CSS effect handles have been removed."""
    if _policy is not None:
        _policy.release(widget)


def can_apply_effect(widget: QWidget) -> bool:
    global _policy
    if _policy is None or not is_qobject_alive(_policy):
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        _policy = _EffectPolicy(app)
    return _policy.can_apply(widget)


class _EffectPolicy(QObject):
    """Share slot observation across engines and standalone effect animations."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.internal_write_depth = 0
        self.scopes: weakref.WeakKeyDictionary[QWidget, _Scope] = weakref.WeakKeyDictionary()
        self.slots: weakref.WeakKeyDictionary[QWidget, _Slot] = weakref.WeakKeyDictionary()
        self.windows: weakref.WeakKeyDictionary[QWidget, weakref.WeakSet[QWidget]] = weakref.WeakKeyDictionary()
        self.dirty_scopes: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self.dirty_slots: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self.blocked_targets: weakref.WeakSet[QWidget] = weakref.WeakSet()

    def can_apply(self, widget: QWidget) -> bool:
        self._flush_changes()
        if widget not in self.scopes:
            self._rebuild_scope(widget)
        self._refresh_slot(widget)
        self._clear_blocked()
        return self.scopes[widget].foreign_count == 0

    def release_unblocked(self, widget: QWidget) -> None:
        scope = self.scopes.get(widget)
        if scope is not None and scope.foreign_count == 0:
            self.release(widget)

    def release(self, widget: QWidget) -> None:
        scope = self.scopes.pop(widget, None)
        self.dirty_scopes.discard(widget)
        self.blocked_targets.discard(widget)
        if scope is None:
            return
        for node_ref in scope.nodes:
            node = node_ref()
            if node is None or (slot := self.slots.get(node)) is None:
                continue
            slot.targets.discard(widget)
            slot.subtree_targets.discard(widget)
            if not slot.targets:
                self._drop_slot(node)

    def _drop_slot(self, widget: QWidget) -> None:
        slot = self.slots.pop(widget, None)
        self.dirty_slots.discard(widget)
        if slot is None:
            return
        if is_qobject_alive(widget):
            widget.removeEventFilter(self)
        if slot.finalizer is not None and slot.finalizer.alive:
            slot.finalizer.detach()
            if is_qobject_alive(widget):
                safe_disconnect(widget.destroyed, slot.finalizer)
        self.dirty_scopes.update(slot.targets)
        window = slot.window_ref() if slot.window_ref is not None else None
        if window is not None and (nodes := self.windows.get(window)) is not None:
            nodes.discard(widget)
            if not nodes:
                del self.windows[window]

    def _watch_slot(self, widget: QWidget) -> _Slot:
        slot = _Slot()
        self.slots[widget] = slot
        owner_ref, widget_ref = weakref.ref(self), weakref.ref(widget)

        def cleanup() -> None:
            owner, node = owner_ref(), widget_ref()
            if owner is not None and node is not None:
                owner.release(node)
                owner._drop_slot(node)

        # Weak maps notice wrapper collection, but not C++ deletion while
        # application code retains the wrapper. Follow the engine's lifetime
        # pattern: connect the finalizer itself, without a wrapping closure.
        finalizer = weakref.finalize(widget, cleanup)
        finalizer.atexit = False
        slot.finalizer = finalizer
        widget.destroyed.connect(finalizer)
        widget.installEventFilter(self)
        return slot

    def _index_slot(self, widget: QWidget, slot: _Slot, window: QWidget) -> None:
        previous = slot.window_ref() if slot.window_ref is not None else None
        if previous is window:
            return
        if previous is not None and (nodes := self.windows.get(previous)) is not None:
            nodes.discard(widget)
            if not nodes:
                del self.windows[previous]
        nodes = self.windows.setdefault(window, weakref.WeakSet())
        nodes.add(widget)
        slot.window_ref = weakref.ref(window)

    def _rebuild_scope(self, widget: QWidget) -> None:
        self.release(widget)
        window = widget.window()
        if window is None:
            return
        subtree = [widget, *(child for child in widget.findChildren(QWidget) if child.window() is window)]
        ancestors: list[QWidget] = []
        ancestor = widget
        while not ancestor.isWindow() and (parent := ancestor.parentWidget()) is not None:
            ancestors.append(parent)
            ancestor = parent
        scope = _Scope(nodes=tuple(weakref.ref(node) for node in (*ancestors, *subtree)))
        self.scopes[widget] = scope
        for nodes, is_subtree in ((ancestors, False), (subtree, True)):
            for node in nodes:
                slot = self.slots.get(node)
                if slot is None:
                    slot = self._watch_slot(node)
                self._index_slot(node, slot, window)
                slot.targets.add(widget)
                if is_subtree:
                    slot.subtree_targets.add(widget)
                scope.foreign_count += slot.foreign
                self._refresh_slot(node)
        if scope.foreign_count:
            self.blocked_targets.add(widget)

    def _refresh_slot(self, widget: QWidget) -> None:
        slot = self.slots.get(widget)
        if slot is None:
            return
        if not slot.targets and widget not in self.scopes:
            self._drop_slot(widget)
            return
        try:
            effect = widget.graphicsEffect()
        except RuntimeError:
            self._drop_slot(widget)
            return
        if (effect is None and slot.effect_ref is None) or (
            effect is not None and slot.effect_ref is not None and slot.effect_ref() is effect
        ):
            return
        foreign = effect is not None and not owns_effect(widget, effect)
        slot.effect_ref = weakref.ref(effect) if effect is not None else None
        if foreign == slot.foreign:
            return
        delta = 1 if foreign else -1
        slot.foreign = foreign
        for target in slot.targets:
            if (scope := self.scopes.get(target)) is not None:
                scope.foreign_count += delta
                if scope.foreign_count:
                    self.blocked_targets.add(target)

    def _flush_changes(self) -> None:
        if not self.dirty_slots and not self.dirty_scopes:
            return
        slots, self.dirty_slots = self.dirty_slots, weakref.WeakSet()
        for widget in slots:
            self._refresh_slot(widget)
        targets, self.dirty_scopes = self.dirty_scopes, weakref.WeakSet()
        for target in targets:
            if is_qobject_alive(target):
                self._rebuild_scope(target)
            else:
                self.release(target)

    def _clear_blocked(self) -> None:
        if not self.blocked_targets:
            return
        targets, self.blocked_targets = self.blocked_targets, weakref.WeakSet()
        for target in targets:
            if not is_qobject_alive(target):
                self.release(target)
                continue
            if (scope := self.scopes.get(target)) is not None and scope.foreign_count:
                clear_engine_effect(target)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        watched, event = a0, a1
        if event is None:
            return False
        event_type = event.type()
        if event_type not in _POLICY_EVENTS or not isinstance(watched, QWidget):
            return False
        if event_type in (QEvent.Type.UpdateRequest, QEvent.Type.Show):
            if event_type == QEvent.Type.Show and not watched.isWindow():
                return False
            self._flush_changes()
            # Installing an unparented application effect emits no child event.
            # Refresh known slots here without rediscovering widget relationships.
            if (nodes := self.windows.get(watched)) is not None:
                for node in tuple(nodes):
                    self._refresh_slot(node)
            self._clear_blocked()
        elif not self.internal_write_depth and (slot := self.slots.get(watched)) is not None:
            if event_type in (QEvent.Type.ChildAdded, QEvent.Type.ChildRemoved):
                self.dirty_slots.add(watched)
                self.dirty_scopes.update(slot.subtree_targets)
            elif event_type == QEvent.Type.ParentChange:
                self.dirty_scopes.update(slot.targets)
                # The new window may have no observer yet. Subscribe immediately
                # rather than waiting for an event from the old hierarchy.
                self._flush_changes()
                self._clear_blocked()
        return False
