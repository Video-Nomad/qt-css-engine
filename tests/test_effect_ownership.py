"""Application-installed graphics effects must remain under application control."""

from collections.abc import Callable, Generator

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine, extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause, ResolvedRuleState
from qt_css_engine.qt_compat import is_qobject_alive, qt_delete
from qt_css_engine.qt_compat.QtCore import (
    QAbstractAnimation,
    QCoreApplication,
    QEvent,
    QMessageLogContext,
    QObject,
    QPointF,
    Qt,
    QtMsgType,
    qInstallMessageHandler,
)
from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.qt_compat.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsDropShadowEffect,
    QGraphicsEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)
from qt_css_engine.style import effect_policy
from qt_css_engine.style.effects import apply_opacity_to_widget, apply_shadow_to_widget
from qt_css_engine.utils.color import ShadowParams


@pytest.fixture
def widget(_app: QApplication) -> Generator[QWidget]:
    widget = QWidget()
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    widget.setProperty("class", "box")
    yield widget
    qt_delete(widget)


@pytest.fixture(params=["opacity", "box-shadow"])
def engine(_app: QApplication, request: pytest.FixtureRequest) -> Generator[TransitionEngine]:
    _, rules = extract_rules("""
        .box {
            opacity: 0.6;
            box-shadow: 0 2px 8px red;
            transition: opacity 1000ms linear, box-shadow 1000ms linear;
        }
        .box:hover { opacity: 1; box-shadow: none; }
    """)
    engine = TransitionEngine(rules, startup_delay_ms=0, effect_priority=request.param)
    yield engine
    qt_delete(engine)


@pytest.fixture(params=[QGraphicsBlurEffect, QGraphicsOpacityEffect, QGraphicsDropShadowEffect])
def install_effect(request: pytest.FixtureRequest) -> Callable[[QWidget], QGraphicsEffect]:
    effect_type: type[QGraphicsEffect] = request.param

    def install(widget: QWidget) -> QGraphicsEffect:
        effect = effect_type(widget)
        if isinstance(effect, QGraphicsBlurEffect):
            effect.setBlurRadius(17)
        elif isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(0.37)
        elif isinstance(effect, QGraphicsDropShadowEffect):
            effect.setOffset(13, -7)
            effect.setBlurRadius(19)
            effect.setColor(QColor("lime"))
        widget.setGraphicsEffect(effect)
        return effect

    return install


def assert_preserved(widget: QWidget, effect: QGraphicsEffect) -> None:
    assert widget.graphicsEffect() is effect
    assert is_qobject_alive(effect)
    assert effect.isEnabled()
    if isinstance(effect, QGraphicsBlurEffect):
        assert effect.blurRadius() == 17
    elif isinstance(effect, QGraphicsOpacityEffect):
        assert effect.opacity() == pytest.approx(0.37)
    elif isinstance(effect, QGraphicsDropShadowEffect):
        assert effect.offset() == QPointF(13, -7)
        assert effect.blurRadius() == 19
        assert effect.color() == QColor("lime")


@pytest.mark.parametrize("priority", ["opacity", "box-shadow"])
@pytest.mark.parametrize("neutral", [False, True])
def test_helpers_preserve_application_effects(
    widget: QWidget, install_effect: Callable[[QWidget], QGraphicsEffect], priority: str, neutral: bool
) -> None:
    effect = install_effect(widget)
    opacity = 1.0 if neutral else 0.2
    shadow = None if neutral else ShadowParams(2, 3, 10, 0, QColor("red"))

    apply_opacity_to_widget(widget, opacity, priority)
    assert_preserved(widget, effect)
    apply_shadow_to_widget(widget, shadow, priority)
    assert_preserved(widget, effect)
    # Also cover opacity attempting to restore the cached CSS shadow.
    apply_opacity_to_widget(widget, 1.0, priority)
    assert_preserved(widget, effect)


def test_existing_application_effect_survives_static_styles_and_animation(
    widget: QWidget, engine: TransitionEngine, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    effect = install_effect(widget)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert_preserved(widget, effect)

    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    for time in (500, 1000):
        for animation in ctx.active_animations.values():
            animation.anim.setCurrentTime(time)
            assert_preserved(widget, effect)


def test_application_replacement_survives_animation_and_orphan_cleanup(
    widget: QWidget, engine: TransitionEngine, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    owned_effect = widget.graphicsEffect()
    assert owned_effect is not None
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    for animation in ctx.active_animations.values():
        assert animation.anim.state() == QAbstractAnimation.State.Running
        animation.anim.setCurrentTime(250)

    effect = install_effect(widget)
    assert not is_qobject_alive(owned_effect)
    for time in (500, 1000):
        for animation in ctx.active_animations.values():
            animation.anim.setCurrentTime(time)
            assert_preserved(widget, effect)

    engine.evaluator.cleanup_orphans(ctx, ResolvedRuleState())
    assert not ctx.active_animations
    assert_preserved(widget, effect)


@pytest.mark.parametrize("installed_first", [False, True])
def test_reload_preserves_application_effects(
    widget: QWidget,
    engine: TransitionEngine,
    install_effect: Callable[[QWidget], QGraphicsEffect],
    installed_first: bool,
) -> None:
    effect = install_effect(widget) if installed_first else None
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    if effect is None:
        effect = install_effect(widget)

    _, rules = extract_rules(".box { opacity: 0.2; box-shadow: 3px 4px 12px blue; }")
    engine.reload_rules(rules)
    assert_preserved(widget, effect)
    QCoreApplication.processEvents()
    assert_preserved(widget, effect)

    engine.reload_rules([])
    assert_preserved(widget, effect)
    QCoreApplication.processEvents()
    assert not engine.get_context(widget).active_animations
    assert_preserved(widget, effect)


def test_deferred_reload_cleanup_preserves_new_application_effect(
    widget: QWidget, engine: TransitionEngine, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    engine.reload_rules([])
    assert widget.graphicsEffect() is None
    effect = install_effect(widget)
    QCoreApplication.processEvents()
    assert_preserved(widget, effect)


def test_shadow_opt_out_preserves_application_effect(
    widget: QWidget, engine: TransitionEngine, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    effect = install_effect(widget)
    widget.setProperty("cssEngineDisableShadow", True)
    engine.evaluator.refresh_shadow_policy(widget)
    assert_preserved(widget, effect)
    assert "box-shadow" not in engine.get_context(widget).active_animations


def test_engine_can_use_slot_after_application_removes_effect(
    widget: QWidget, engine: TransitionEngine, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    effect = install_effect(widget)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert_preserved(widget, effect)

    widget.setGraphicsEffect(None)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    owned_effect = widget.graphicsEffect()
    if engine.effect_priority == "opacity":
        assert isinstance(owned_effect, QGraphicsOpacityEffect)
        assert owned_effect.opacity() == pytest.approx(0.6)
    else:
        assert isinstance(owned_effect, QGraphicsDropShadowEffect)
        assert owned_effect.color() == QColor("red")


@pytest.mark.parametrize("priority", ["opacity", "box-shadow"])
@pytest.mark.parametrize("placement", ["ancestor", "descendant"])
def test_helpers_do_not_nest_effects_with_application_effects(
    widget: QWidget, install_effect: Callable[[QWidget], QGraphicsEffect], priority: str, placement: str
) -> None:
    child = QWidget(widget)
    target, application_widget = (child, widget) if placement == "ancestor" else (widget, child)
    effect = install_effect(application_widget)

    apply_opacity_to_widget(target, 0.5, priority)
    assert target.graphicsEffect() is None
    assert_preserved(application_widget, effect)
    apply_shadow_to_widget(target, ShadowParams(2, 3, 10, 0, QColor("red")), priority)
    assert target.graphicsEffect() is None
    assert_preserved(application_widget, effect)


@pytest.mark.parametrize("placement", ["ancestor", "descendant"])
def test_existing_engine_effect_yields_to_application_effect_in_hierarchy(
    widget: QWidget,
    engine: TransitionEngine,
    install_effect: Callable[[QWidget], QGraphicsEffect],
    placement: str,
) -> None:
    child = QWidget(widget)
    child.setProperty("class", "box")
    target, application_widget = (child, widget) if placement == "ancestor" else (widget, child)
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    owned_effect = target.graphicsEffect()
    assert owned_effect is not None

    effect = install_effect(application_widget)
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    assert target.graphicsEffect() is None
    assert not is_qobject_alive(owned_effect)
    assert_preserved(application_widget, effect)


@pytest.mark.parametrize("placement", ["ancestor", "descendant"])
def test_update_request_removes_conflicting_static_engine_effect(
    _app: QApplication,
    widget: QWidget,
    engine: TransitionEngine,
    install_effect: Callable[[QWidget], QGraphicsEffect],
    placement: str,
) -> None:
    child = QWidget(widget)
    child.setProperty("class", "box")
    target, application_widget = (child, widget) if placement == "ancestor" else (widget, child)
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    owned_effect = target.graphicsEffect()
    assert owned_effect is not None

    effect = install_effect(application_widget)
    _app.installEventFilter(engine)
    try:
        # No animation tick or CSS evaluation runs between application installation and repaint.
        QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
        assert target.graphicsEffect() is None
        assert not is_qobject_alive(owned_effect)
        assert_preserved(application_widget, effect)
    finally:
        _app.removeEventFilter(engine)


def test_application_effect_on_sibling_does_not_block_engine_effect(
    widget: QWidget, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    sibling = QWidget(widget)
    target = QWidget(widget)
    effect = install_effect(sibling)
    apply_opacity_to_widget(target, 0.5, "opacity")
    opacity = target.graphicsEffect()
    assert isinstance(opacity, QGraphicsOpacityEffect)
    assert opacity.opacity() == 0.5
    assert_preserved(sibling, effect)


@pytest.mark.parametrize("placement", ["ancestor", "descendant"])
def test_application_effect_in_separate_window_does_not_block_engine_effect(
    widget: QWidget, install_effect: Callable[[QWidget], QGraphicsEffect], placement: str
) -> None:
    window = QWidget(widget, Qt.WindowType.Window)
    target, application_widget = (window, widget) if placement == "ancestor" else (widget, window)
    effect = install_effect(application_widget)
    apply_opacity_to_widget(target, 0.5, "opacity")
    opacity = target.graphicsEffect()
    assert isinstance(opacity, QGraphicsOpacityEffect)
    assert opacity.opacity() == 0.5
    assert_preserved(application_widget, effect)


@pytest.mark.parametrize("target_class", ["widget-container", "label"])
@pytest.mark.parametrize(
    "declaration", ["box-shadow: 2px 2px 8px black", "text-shadow: 1px 1px 2px black", "opacity: 0.5"]
)
def test_lyrics_hierarchy_renders_without_painter_warnings(
    _app: QApplication, widget: QWidget, qtbot: QtBot, target_class: str, declaration: str
) -> None:
    # YASB puts its fade effect on the outer widget; the CSS classes belong to descendants.
    frame = QFrame(widget)
    frame.setProperty("class", "widget lyrics-widget")
    container = QFrame(frame)
    container.setProperty("class", "widget-container")
    label = QLabel("Lyrics preview", container)
    label.setProperty("class", "label")
    for parent, child in ((widget, frame), (frame, container), (container, label)):
        layout = QHBoxLayout(parent)
        layout.addWidget(child)
    opacity = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(opacity)
    opacity.setOpacity(1.0)
    widget.resize(240, 70)
    css, rules = extract_rules(f"""
        .lyrics-widget {{ background-color: red; }}
        .lyrics-widget .{target_class} {{ {declaration}; }}
        .lyrics-widget .label {{ color: white; transition: color 1000ms linear; }}
        .lyrics-widget .label:hover {{ color: lime; }}
    """)
    widget.setStyleSheet(css)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    messages: list[str] = []

    def record_message(_kind: QtMsgType, _context: QMessageLogContext, message: str | None) -> None:
        if message is not None:
            messages.append(message)

    _app.installEventFilter(engine)
    previous = qInstallMessageHandler(record_message)
    try:
        widget.show()
        qtbot.wait(20)
        handle = widget.windowHandle()
        assert handle is None or not handle.isExposed()
        for value in (1.0, 0.5, 1.0):
            opacity.setOpacity(value)
            image = widget.grab()
            assert not image.isNull()
            assert widget.graphicsEffect() is opacity
            assert container.graphicsEffect() is None
            assert label.graphicsEffect() is None
        # Color transitions still run within the protected hierarchy.
        ctx = engine.get_context(label)
        ctx.active_pseudos.add(":hover")
        engine.evaluate_widget_state(label)
        color = ctx.active_animations["color"]
        color.anim.setCurrentTime(1000)
        assert ctx.css_anim_props["color"] == QColor("lime").name(QColor.NameFormat.HexArgb)
        assert not [message for message in messages if "QPainter::" in message or "Painter not active" in message]
    finally:
        qInstallMessageHandler(previous)
        _app.removeEventFilter(engine)
        qt_delete(engine)


class SlotCountingWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.effect_reads = 0

    def graphicsEffect(self) -> QGraphicsEffect | None:
        self.effect_reads += 1
        return super().graphicsEffect()


def forbid_discovery(*_args: object, **_kwargs: object) -> list[QWidget]:
    raise AssertionError("Unexpected widget discovery during an ordinary tick or repaint")


def test_neutral_css_effects_do_not_discover_widgets(widget: QWidget, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(widget, "findChildren", forbid_discovery)
    apply_opacity_to_widget(widget, 1.0, "opacity")
    apply_shadow_to_widget(widget, None, "opacity")
    assert widget.graphicsEffect() is None


def test_animation_ticks_and_repaints_use_cached_slots(
    widget: QWidget, engine: TransitionEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = QWidget(widget)
    target.setProperty("class", "box")
    unrelated = SlotCountingWidget(widget)
    for _ in range(100):
        SlotCountingWidget(unrelated)
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    monkeypatch.setattr(widget, "findChildren", forbid_discovery)
    monkeypatch.setattr(target, "findChildren", forbid_discovery)

    ctx = engine.get_context(target)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(target)
    for time in (100, 200, 300):
        for animation in ctx.active_animations.values():
            animation.anim.setCurrentTime(time)
        QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert unrelated.effect_reads == 0
    assert all(child.effect_reads == 0 for child in unrelated.findChildren(SlotCountingWidget))

    # An unrelated sibling being created must not invalidate the target's cached branch.
    sibling = SlotCountingWidget(widget)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert sibling.effect_reads == 0


def test_updates_to_unrelated_windows_do_not_check_effect_slots(widget: QWidget) -> None:
    apply_opacity_to_widget(widget, 0.5, "opacity")
    unrelated = SlotCountingWidget()
    try:
        for _ in range(10):
            QCoreApplication.sendEvent(unrelated, QEvent(QEvent.Type.UpdateRequest))
        assert unrelated.effect_reads == 0
    finally:
        qt_delete(unrelated)


def test_cached_descendant_slot_detects_unparented_application_effect(
    widget: QWidget, engine: TransitionEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = QWidget(widget)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    monkeypatch.setattr(widget, "findChildren", forbid_discovery)

    effect = QGraphicsOpacityEffect()
    effect.setOpacity(0.37)
    child.setGraphicsEffect(effect)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert widget.graphicsEffect() is None
    assert_preserved(child, effect)


def test_new_child_invalidates_only_the_affected_effect_scope(widget: QWidget, engine: TransitionEngine) -> None:
    target = SlotCountingWidget(widget)
    target.setProperty("class", "box")
    unrelated = SlotCountingWidget(widget)
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    child = QWidget(target)
    effect = QGraphicsOpacityEffect()
    effect.setOpacity(0.37)
    child.setGraphicsEffect(effect)

    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert target.graphicsEffect() is None
    assert_preserved(child, effect)
    assert unrelated.effect_reads == 0


def test_reparenting_rebuilds_cached_effect_dependencies(
    widget: QWidget, engine: TransitionEngine, install_effect: Callable[[QWidget], QGraphicsEffect]
) -> None:
    target = QWidget(widget)
    target.setProperty("class", "box")
    application_widget = QWidget(widget)
    effect = install_effect(application_widget)
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    assert target.graphicsEffect() is not None

    target.setParent(application_widget)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert target.graphicsEffect() is None
    assert_preserved(application_widget, effect)

    target.setParent(widget)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    assert target.graphicsEffect() is not None
    assert_preserved(application_widget, effect)


def test_destroyed_application_effect_unblocks_cached_scope(widget: QWidget, engine: TransitionEngine) -> None:
    child = QWidget(widget)
    effect = QGraphicsOpacityEffect()
    child.setGraphicsEffect(effect)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert widget.graphicsEffect() is None

    qt_delete(effect)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert widget.graphicsEffect() is not None


def test_removing_css_releases_observed_slots(
    widget: QWidget, engine: TransitionEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = SlotCountingWidget(widget)
    target.setProperty("class", "box")
    engine.evaluate_widget_state(target, cause=EvaluationCause.POLISH)
    engine.evaluator.cleanup_orphans(engine.get_context(target), ResolvedRuleState())
    target.effect_reads = 0
    monkeypatch.setattr(widget, "findChildren", forbid_discovery)
    monkeypatch.setattr(target, "findChildren", forbid_discovery)
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert target.effect_reads == 0


def test_destroyed_target_with_retained_wrapper_does_not_break_other_targets(widget: QWidget) -> None:
    retired = QWidget(widget)
    survivor = QWidget(widget)
    apply_opacity_to_widget(retired, 0.5, "opacity")
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))

    qt_delete(retired)
    assert not is_qobject_alive(retired)
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.37)
    widget.setGraphicsEffect(effect)

    # Retaining the Python wrapper must not leave a dead target in the dependency cache.
    apply_opacity_to_widget(survivor, 0.5, "opacity")
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    assert survivor.graphicsEffect() is None
    assert_preserved(widget, effect)


def test_engine_effect_replacement_reuses_hierarchy_cache(widget: QWidget, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_opacity_to_widget(widget, 0.5, "opacity")
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))
    monkeypatch.setattr(widget, "findChildren", forbid_discovery)
    shadow = ShadowParams(2, 3, 10, 0, QColor("red"))

    for _ in range(3):
        apply_shadow_to_widget(widget, shadow, "box-shadow")
        assert isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect)
        apply_opacity_to_widget(widget, 0.5, "opacity")
        assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)
        QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))


@pytest.fixture
def policy_event_receivers(monkeypatch: pytest.MonkeyPatch) -> Generator[list[int]]:
    """Record actual Qt-to-Python dispatch, including events rejected by the policy."""
    receivers: list[int] = []

    class RecordingPolicy(effect_policy._EffectPolicy):  # pyright: ignore[reportPrivateUsage]
        def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
            if a0 is not None and a1 is not None and a1.type() == QEvent.Type.User:
                receivers.append(id(a0))
            return super().eventFilter(a0, a1)

    monkeypatch.setattr(effect_policy, "_EffectPolicy", RecordingPolicy)
    monkeypatch.setattr(effect_policy, "_policy", None)
    yield receivers
    policy = effect_policy._policy  # pyright: ignore[reportPrivateUsage]
    if policy is not None:
        for target in list(policy.scopes):
            policy.release(target)
        qt_delete(policy)


def test_policy_receives_events_only_for_observed_widgets(
    _app: QApplication, widget: QWidget, policy_event_receivers: list[int]
) -> None:
    branch = QWidget(widget)
    target = QWidget(branch)
    descendant = QWidget(target)
    sibling = QWidget(widget)
    separate_window = QWidget(widget, Qt.WindowType.Window)
    non_widget = QObject(target)
    apply_opacity_to_widget(target, 0.5, "opacity")
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))

    observed = [widget, branch, target, descendant]
    for receiver in observed:
        QCoreApplication.sendEvent(receiver, QEvent(QEvent.Type.User))
    assert policy_event_receivers == [id(receiver) for receiver in observed]

    policy_event_receivers.clear()
    for receiver in (sibling, separate_window, non_widget, _app):
        QCoreApplication.sendEvent(receiver, QEvent(QEvent.Type.User))
    assert policy_event_receivers == []


def test_policy_releases_filters_only_after_last_dependent_target(
    widget: QWidget, policy_event_receivers: list[int]
) -> None:
    branch = QWidget(widget)
    first, second = QWidget(branch), QWidget(branch)
    apply_opacity_to_widget(first, 0.5, "opacity")
    apply_opacity_to_widget(second, 0.5, "opacity")

    apply_opacity_to_widget(first, 1.0, "opacity")
    for receiver in (widget, branch, first, second):
        QCoreApplication.sendEvent(receiver, QEvent(QEvent.Type.User))
    assert policy_event_receivers == [id(widget), id(branch), id(second)]

    policy_event_receivers.clear()
    apply_opacity_to_widget(second, 1.0, "opacity")
    for receiver in (widget, branch, first, second):
        QCoreApplication.sendEvent(receiver, QEvent(QEvent.Type.User))
    assert policy_event_receivers == []

    # Reactivating a released target must reconnect exactly one observer per node.
    apply_opacity_to_widget(first, 0.5, "opacity")
    for receiver in (widget, branch, first, second):
        QCoreApplication.sendEvent(receiver, QEvent(QEvent.Type.User))
    assert policy_event_receivers == [id(widget), id(branch), id(first)]


@pytest.mark.parametrize("move_ancestor", [False, True], ids=["target", "ancestor"])
@pytest.mark.parametrize("effect_before_move", [False, True], ids=["new-effect", "existing-effect"])
def test_policy_follows_reparenting_to_unobserved_window(
    widget: QWidget, policy_event_receivers: list[int], move_ancestor: bool, effect_before_move: bool
) -> None:
    branch = QWidget(widget)
    target = QWidget(branch)
    destination = QWidget(widget, Qt.WindowType.Window)
    new_parent = QWidget(destination)
    effect = QGraphicsOpacityEffect()
    effect.setOpacity(0.37)
    if effect_before_move:
        new_parent.setGraphicsEffect(effect)
    apply_opacity_to_widget(target, 0.5, "opacity")
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.UpdateRequest))

    moving = branch if move_ancestor else target
    moving.setParent(new_parent)
    if not effect_before_move:
        new_parent.setGraphicsEffect(effect)
    # No event processing, evaluation, or repaint of the old window intervenes.
    QCoreApplication.sendEvent(destination, QEvent(QEvent.Type.UpdateRequest))
    assert target.graphicsEffect() is None
    assert_preserved(new_parent, effect)

    for receiver in (widget, destination, new_parent, target):
        QCoreApplication.sendEvent(receiver, QEvent(QEvent.Type.User))
    assert policy_event_receivers == [id(destination), id(new_parent), id(target)]

    # Children added after the move must also become observed in the new window.
    child = QWidget(target)
    QCoreApplication.sendEvent(destination, QEvent(QEvent.Type.UpdateRequest))
    policy_event_receivers.clear()
    QCoreApplication.sendEvent(child, QEvent(QEvent.Type.User))
    assert policy_event_receivers == [id(child)]
