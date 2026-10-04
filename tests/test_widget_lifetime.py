"""The engine must observe widgets without becoming their owner."""

import gc
import weakref

import pytest

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.factory import Animation, create_animator
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.handlers import polish as polish_handler
from qt_css_engine.engine.handlers import reload as reload_handler
from qt_css_engine.qt_compat import is_qobject_alive, qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QCoreApplication, QEasingCurve, QEvent
from qt_css_engine.qt_compat.QtWidgets import QApplication, QCheckBox, QWidget
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.writer import StyleWriter


def make_engine(css: str = "") -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def drain_events() -> None:
    for _ in range(3):
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def assert_collected[W: QWidget](engine: TransitionEngine, wid: int, widget_ref: weakref.ref[W]) -> None:
    gc.collect()
    # Check before processing events: even pending callbacks must not keep the widget alive.
    assert widget_ref() is None
    assert wid not in engine.store.widgets
    assert wid not in engine.store.contexts
    assert wid not in engine.active_rule_widgets
    assert wid not in engine.connected_checkable_ids
    assert wid not in engine.widget_finalizers
    assert wid not in engine.matcher.widget_cache.rules
    drain_events()


def test_detached_polished_widget_is_collected(_app: QApplication) -> None:
    engine = make_engine(".box:hover { color: red; transition: color 100ms; }")
    parent = QWidget()
    _app.installEventFilter(engine)
    try:
        widget = QWidget(parent)
        widget.setProperty("class", "box")
        widget.ensurePolished()
        drain_events()
        wid = id(widget)
        widget_ref = weakref.ref(widget)
        destroyed: list[int] = []
        widget.destroyed.connect(lambda: destroyed.append(wid))
        assert wid in engine.store.contexts
        assert not engine.store.contexts[wid].active_animations

        widget.setParent(None)
        del widget

        assert_collected(engine, wid, widget_ref)
        assert destroyed == [wid]
    finally:
        _app.removeEventFilter(engine)
        qt_delete(parent)


@pytest.mark.parametrize("tracking", ["context", "active", "checkable", "focus"])
def test_detached_widget_tracking_does_not_retain_widget(_app: QApplication, tracking: str) -> None:
    engine = (
        make_engine(".box:active { color: red; transition: color 100ms; }") if tracking == "active" else make_engine()
    )
    parent = QWidget()
    widget = QCheckBox(parent) if tracking == "checkable" else QWidget(parent)
    widget.setProperty("class", "box")
    wid = id(widget)
    widget_ref = weakref.ref(widget)
    if tracking == "checkable":
        engine.connect_checkable(widget)
        assert wid in engine.connected_checkable_ids
        assert wid not in engine.store.contexts
    elif tracking == "active":
        engine.seed_active_pseudo(widget)
        assert wid in engine.active_rule_widgets
    elif tracking == "focus":
        _app.installEventFilter(engine)
        QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.FocusIn))
        _app.removeEventFilter(engine)
        assert wid in engine.store.contexts
    else:
        engine.get_context(widget)

    widget.setParent(None)
    del widget

    assert_collected(engine, wid, widget_ref)
    qt_delete(parent)


@pytest.mark.parametrize("finished", [False, True], ids=["running", "finished"])
@pytest.mark.parametrize(
    ("prop", "initial", "target"),
    [
        ("background-color", "red", "blue"),
        ("min-width", "10px", "20px"),
        ("opacity", "1", "0.5"),
        ("box-shadow", "none", "2px 2px 4px black"),
    ],
)
def test_detached_animated_widget_is_collected(
    _app: QApplication, prop: str, initial: str, target: str, finished: bool
) -> None:
    engine = make_engine(f".box {{ {prop}: {initial}; }} .box:hover {{ {prop}: {target}; transition: {prop} 1000ms; }}")
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    animation = ctx.active_animations[prop]
    assert animation.anim.state() == QAbstractAnimation.State.Running
    if finished:
        animation.anim.setCurrentTime(animation.anim.duration())
        assert animation.anim.state() == QAbstractAnimation.State.Stopped
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget
    gc.collect()

    assert widget_ref() is None
    assert animation.anim.state() == QAbstractAnimation.State.Stopped
    assert not ctx.active_animations
    assert_collected(engine, wid, widget_ref)
    assert not is_qobject_alive(animation)
    qt_delete(parent)


@pytest.mark.parametrize("cause", [EvaluationCause.CLASS_CHANGE, EvaluationCause.CLICKED_ACTIVATION])
def test_animation_finished_callbacks_do_not_retain_widget(_app: QApplication, cause: EvaluationCause) -> None:
    engine = make_engine(".box { color: red; } .box:clicked { color: blue; transition: color 1000ms; }")
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    updated = {":clicked"}
    engine.prepare_clicked(widget, ctx, updated)
    ctx.active_pseudos = updated
    engine.evaluate_widget_state(widget, cause=cause)
    assert ctx.class_anim_callbacks if cause == EvaluationCause.CLASS_CHANGE else ctx.clicked_anim_callbacks
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget

    assert_collected(engine, wid, widget_ref)
    assert not ctx.class_anim_callbacks
    assert not ctx.clicked_anim_callbacks
    qt_delete(parent)


def test_pending_transition_delay_does_not_retain_widget(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; } .box:hover { color: blue; transition: color 100ms 60s; }")
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    timer = ctx.pending_delays["color"]
    assert timer.isActive()
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget
    gc.collect()

    assert widget_ref() is None
    assert not timer.isActive()
    assert not ctx.pending_delays
    assert_collected(engine, wid, widget_ref)
    assert not is_qobject_alive(timer)
    qt_delete(parent)


def test_pending_polish_does_not_retain_widget(_app: QApplication) -> None:
    engine = make_engine(".box:hover { color: red; transition: color 100ms; }")
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    engine.queue_polish_evaluation(widget, force=True)
    assert engine.polish.pending
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget

    gc.collect()
    assert not engine.polish.forced_widgets
    assert_collected(engine, wid, widget_ref)
    assert not engine.polish.queue
    assert not engine.polish.forced_widgets
    qt_delete(parent)


@pytest.mark.parametrize("force_replacement", [False, True])
def test_polish_force_flag_does_not_transfer_to_reused_id(
    _app: QApplication, monkeypatch: pytest.MonkeyPatch, force_replacement: bool
) -> None:
    engine = make_engine(".box { color: red; } .box:hover { color: blue; transition: color 1000ms; }")
    parent = QWidget()
    retired = QWidget(parent)
    retired_id = id(retired)
    retired_ref = weakref.ref(retired)
    engine.queue_polish_evaluation(retired, force=True)
    retired.setParent(None)
    del retired
    gc.collect()
    assert retired_ref() is None

    replacement = QWidget(parent)
    replacement.setProperty("class", "box")
    ctx = engine.get_context(replacement)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(replacement)
    animation = ctx.active_animations["color"]
    assert animation.anim.state() == QAbstractAnimation.State.Running

    # Simulate allocator ID reuse in queue bookkeeping without depending on allocation timing.
    def reused_id(_widget: QWidget) -> int:
        return retired_id

    monkeypatch.setattr(polish_handler, "id", reused_id, raising=False)
    engine.queue_polish_evaluation(replacement, force=force_replacement)
    engine.polish.flush(engine)

    expected = QAbstractAnimation.State.Stopped if force_replacement else QAbstractAnimation.State.Running
    assert animation.anim.state() == expected
    qt_delete(parent)


def test_reload_exclusion_does_not_transfer_to_reused_id(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(".retired { color: red; } .retired:hover { color: blue; transition: color 1000ms; }")
    parent = QWidget()
    retired = QWidget(parent)
    retired.setProperty("class", "retired")
    ctx = engine.get_context(retired)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(retired)
    assert ctx.active_animations
    retired_id = id(retired)
    retired_ref = weakref.ref(retired)
    _, rules = extract_rules(".replacement { opacity: 0.5; }")
    engine.reload_rules(rules)
    retired.setParent(None)
    del retired
    gc.collect()
    assert retired_ref() is None

    replacement = QWidget(parent)
    replacement.setProperty("class", "replacement")
    assert replacement.graphicsEffect() is None

    # Reuse the retired widget's key during the deferred reload sweep.
    def reused_id(_widget: QWidget) -> int:
        return retired_id

    monkeypatch.setattr(reload_handler, "id", reused_id, raising=False)
    drain_events()

    assert replacement.graphicsEffect() is not None
    qt_delete(parent)


@pytest.mark.parametrize("bound", [False, True], ids=["standalone", "engine"])
def test_pending_style_flush_does_not_retain_widget(_app: QApplication, bound: bool) -> None:
    engine = make_engine()
    parent = QWidget()
    widget = QWidget(parent)
    ctx = engine.get_context(widget) if bound else WidgetState()
    ctx.css_anim_props["color"] = "red"
    writer = engine.writer if bound else StyleWriter()
    writer.schedule(widget, ctx)
    assert ctx.style_flush_pending
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget

    assert_collected(engine, wid, widget_ref)
    assert not ctx.style_flush_pending
    qt_delete(parent)


def test_pending_clicked_deactivation_does_not_retain_widget(_app: QApplication) -> None:
    engine = make_engine(".box:clicked { color: blue; }")
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":clicked")
    engine.finish_clicked_activation(widget, ctx)
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget

    assert_collected(engine, wid, widget_ref)
    qt_delete(parent)


def test_pending_effect_reload_does_not_retain_widget(_app: QApplication) -> None:
    css = ".box { opacity: 1; } .box:hover { opacity: 0.5; transition: opacity 1000ms; }"
    engine = make_engine(css)
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    assert ctx.active_animations
    _, rules = extract_rules(css)
    engine.reload_rules(rules)
    wid = id(widget)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget

    assert_collected(engine, wid, widget_ref)
    qt_delete(parent)


def test_detached_widget_with_application_reference_still_animates(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; } .box:hover { color: blue; transition: color 1000ms; }")
    parent = QWidget()
    widget = QWidget(parent)
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)

    widget.setParent(None)
    gc.collect()
    assert is_qobject_alive(widget)
    assert engine.store.get(widget) is ctx
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    animation: Animation = ctx.active_animations["color"]
    assert animation.widget is widget
    assert animation.anim.state() == QAbstractAnimation.State.Running
    widget.setParent(parent)
    assert engine.store.get(widget) is ctx
    assert is_qobject_alive(widget)

    qt_delete(parent)


def test_qt_parent_keeps_tracked_widget_alive_without_application_reference(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; } .box:hover { color: blue; transition: color 1000ms; }")
    parent = QWidget()
    widget = QCheckBox(parent)
    widget.setProperty("class", "box")
    engine.connect_checkable(widget)
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":hover")
    engine.evaluate_widget_state(widget)
    wid = id(widget)
    widget_ref = weakref.ref(widget)
    del widget
    gc.collect()

    assert widget_ref() is not None
    assert is_qobject_alive(widget_ref())
    assert wid in engine.store.contexts
    assert ctx.active_animations["color"].anim.state() == QAbstractAnimation.State.Running

    qt_delete(parent)
    assert_collected(engine, wid, widget_ref)


@pytest.mark.parametrize(
    ("prop", "initial", "target"),
    [
        ("color", "red", "blue"),
        ("min-width", "10px", "20px"),
        ("opacity", "1", "0.5"),
        ("box-shadow", "none", "2px 2px 4px black"),
    ],
)
def test_standalone_animation_callbacks_after_widget_collection(
    _app: QApplication, prop: str, initial: str, target: str
) -> None:
    parent = QWidget()
    widget = QWidget(parent)
    animation = create_animator(widget, prop, initial, 1000, QEasingCurve.Type.Linear)
    assert animation is not None
    assert animation.set_target(target)
    widget_ref = weakref.ref(widget)

    widget.setParent(None)
    del widget
    gc.collect()

    assert widget_ref() is None
    animation.anim.setCurrentTime(500)
    assert animation.anim.state() == QAbstractAnimation.State.Stopped
    # A queued completion signal must also tolerate the Python wrapper having disappeared.
    animation.anim.finished.emit()
    qt_delete(animation)
    qt_delete(parent)
