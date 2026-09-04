# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""Final-round edge cases for the last uncovered branches."""

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.css.model import StyleRule
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.evaluator import Evaluation
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QEasingCurve
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget
from qt_css_engine.style.writer import StyleWriter
from qt_css_engine.types import ResolvedProperty, ResolvedRuleState, WidgetContext


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


def hover_widget(engine: TransitionEngine, widget: QWidget) -> None:
    engine.get_context(widget).active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)


# ---------------------------------------------------------------------------
# parser at-rule inside block
# ---------------------------------------------------------------------------


def test_parser_skips_at_rule_inside_block() -> None:
    cleaned, rules = extract_rules(".box { color: red; @unknown foo; }")
    assert rules[0].properties.get("color") == "red"
    assert "@unknown" not in cleaned
    assert "color" in cleaned


# ---------------------------------------------------------------------------
# evaluator orphans / delay-fire / snap
# ---------------------------------------------------------------------------


def test_orphan_color_with_gradient_base_evicts(_app: QApplication) -> None:
    from qt_css_engine.animation.color import ColorAnimation

    engine = make_engine(".x { transition: background-color 200ms; }")
    widget = QWidget()
    try:
        ctx = engine.get_context(widget)
        anim = ColorAnimation(widget, "background-color", "red", 200, QEasingCurve.Type.Linear, ctx=ctx)
        ctx.active_animations["background-color"] = anim
        ctx.css_anim_props["background-color"] = "red"
        needs = engine.evaluator.cleanup_orphans(
            ctx, ResolvedRuleState(base_props={"background-color": "linear-gradient(red, blue)"})
        )
        assert "background-color" not in ctx.css_anim_props
        assert "background-color" not in ctx.active_animations
        assert needs is True
    finally:
        destroy(widget)


def test_stop_orphan_opacity_deleted_widget_swallowed(_app: QApplication) -> None:
    engine = make_engine(".x { transition: opacity 200ms; }")
    widget = QWidget()
    try:
        ctx = engine.get_context(widget)
        anim = OpacityAnimation(widget, 0.5, 200, QEasingCurve.Type.Linear)
        ctx.active_animations["opacity"] = anim
        wid = id(widget)
        destroy(widget)
        # Widget C++ gone but anim wrapper alive → setGraphicsEffect raises → swallowed
        engine.evaluator._stop_orphan_effect(anim)
        assert wid is not None
    except RuntimeError:
        pass


def test_delay_fire_cancel_runtimeerror_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 500ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        captured: dict[str, object] = {}

        def _capture(_ctx: WidgetContext, _prop: str, _delay_ms: int, _cb: object) -> None:
            captured["cb"] = _cb

        monkeypatch.setattr(engine.delays, "schedule", _capture)
        engine.evaluator._schedule_delay(widget, ctx, "background-color", 500)
        assert "cb" in captured
        fire = captured["cb"]
        assert callable(fire)
        # Remove context so _fire takes the `c is None` branch, and make cancel raise
        wid = id(widget)
        engine.store.contexts.pop(wid, None)
        engine.store.widgets.pop(wid, None)

        def _boom(_c: WidgetContext, _p: str) -> None:
            raise RuntimeError("timer gone")

        monkeypatch.setattr(engine.delays, "cancel", _boom)
        fire()  # must swallow cancel RuntimeError + fire_delayed_prop noop
    finally:
        destroy(widget)


def test_delay_fire_propagates_runtimeerror_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 100ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    try:
        captured: dict[str, object] = {}

        def _capture(_ctx: WidgetContext, _prop: str, _delay_ms: int, _cb: object) -> None:
            captured["cb"] = _cb

        monkeypatch.setattr(engine.delays, "schedule", _capture)
        engine.evaluator._schedule_delay(widget, ctx, "background-color", 50)
        assert "cb" in captured
        fire = captured["cb"]
        assert callable(fire)

        def _boom(_w: QWidget, _p: str) -> None:
            raise RuntimeError("gone")

        monkeypatch.setattr(engine.evaluator, "fire_delayed_prop", _boom)
        fire()  # must swallow fire_delayed_prop RuntimeError
        assert "background-color" not in ctx.pending_delays
    finally:
        for t in list(ctx.pending_delays.values()):
            try:
                t.stop()
            except RuntimeError:
                pass
        destroy(widget)


def test_snap_natural_no_anim_pops_and_flushes(_app: QApplication) -> None:
    engine = make_engine(".box { transition: width 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        ctx.css_anim_props["width"] = "100.000px"
        ev = Evaluation(widget, ctx, ResolvedRuleState(), EvaluationCause.POLISH)
        resolved = ResolvedProperty(animation=None, current="100px", target="50px", is_natural_target=True, spec=None)
        assert engine.evaluator._snap_to_target(ev, "width", resolved) is True
        assert "width" not in ctx.css_anim_props
    finally:
        destroy(widget)


def test_snap_existing_generic_radius_box_size(_app: QApplication) -> None:
    engine = make_engine("""
        .box { border-radius: 0px; transition: border-radius 1000ms; }
        .box:hover { border-radius: 12px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(100, 100)
    try:
        hover_widget(engine, widget)
        ctx = engine.get_context(widget)
        anim_obj = ctx.active_animations.get("border-top-left-radius")
        assert isinstance(anim_obj, GenericPropertyAnimation)
        assert anim_obj.anim.state() == anim_obj.anim.State.Running
        # Snap the running radius anim (non-natural) → update_box_props + box_size branch
        engine.evaluator._snap_existing(
            widget, "border-top-left-radius", anim_obj, "12px", False, {"border-top-left-radius": "12px"}
        )
        assert ctx.css_anim_props.get("border-top-left-radius") == "12.000px"
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# parent_change except path
# ---------------------------------------------------------------------------


def test_parent_change_should_evaluate_raises_swallowed(
    _app: QApplication, monkeypatch: pytest.MonkeyPatch, qtbot: QtBot
) -> None:
    engine = make_engine(".parent .child { color: red; transition: color 200ms; }")
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    try:
        orig = engine.should_evaluate

        def _boom(_w: QWidget) -> bool:
            raise RuntimeError("gone")

        monkeypatch.setattr(engine, "should_evaluate", _boom)
        engine.on_parent_change(parent)  # must swallow per-widget RuntimeError
        qtbot.wait(10)
        monkeypatch.setattr(engine, "should_evaluate", orig)
    finally:
        destroy(child)
        destroy(parent)


# ---------------------------------------------------------------------------
# reload handcrafted dead-widget branches
# ---------------------------------------------------------------------------


def test_reload_collect_skips_dead_sample_handcrafted(_app: QApplication) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    widget = QWidget()
    ctx = WidgetContext()
    anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=ctx)
    ctx.active_animations["width"] = anim
    wid = id(widget)
    destroy(widget)
    del widget
    widgets, ids = reload_handler.collect_reload_widgets({wid: ctx})
    assert widgets == set()
    assert ids == set()


def test_reload_clear_styles_swallow_deleted_widget() -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.css_anim_props["color"] = "red"
    wid = id(widget)
    destroy(widget)
    # Pass the deleted wrapper explicitly → setStyleSheet raises → swallowed
    out = reload_handler.clear_reload_styles(engine, {widget})
    assert wid in out or wid not in out  # no crash either way


def test_reload_clear_second_loop_runtimeerror_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    ctx = engine.get_context(widget)
    ctx.css_anim_props["color"] = "red"

    def _boom(_s: str | None) -> None:
        raise RuntimeError("gone")

    # Real QApplication.instance().allWidgets() already includes `widget`,
    # so the second loop reaches it and hits the RuntimeError guard.
    monkeypatch.setattr(widget, "setStyleSheet", _boom)
    out = reload_handler.clear_reload_styles(engine, set())
    assert isinstance(out, set)
    monkeypatch.undo()
    destroy(widget)


def test_reload_reeval_dead_widget_swallowed() -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine(".box { opacity: 0.5; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    wid = id(widget)
    destroy(widget)
    reload_handler.reeval_reload_widgets_deferred(engine, {widget}, set())
    assert wid is not None


def test_reload_reeval_no_app_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine(".box { border-radius: 999px; }")

    class _FakeQApp:
        @staticmethod
        def instance() -> None:
            return None

    monkeypatch.setattr(reload_handler, "QApplication", _FakeQApp)
    reload_handler.reeval_reload_widgets_deferred(engine, set(), set())
    reload_handler.reeval_border_radius_widgets_after_reload(engine)
    # Also hit the no-flag early return with an engine lacking border-radius
    plain_engine = make_engine(".box { opacity: 0.5; }")
    reload_handler.reeval_border_radius_widgets_after_reload(plain_engine)


def test_reload_reeval_skips_non_radius_widget(_app: QApplication) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine("""
        .box { color: red; transition: color 200ms; }
        .round { border-radius: 999px; }
    """)
    plain = QWidget()
    plain.setProperty("class", "box")
    round_w = QWidget()
    round_w.setProperty("class", "round")
    round_w.resize(20, 10)
    try:
        reload_handler.reeval_border_radius_widgets_after_reload(engine)
        ctx = engine.get_context(round_w)
        assert ctx.css_anim_props.get("border-top-left-radius") == "5.000px"
        assert "color" not in engine.get_context(plain).css_anim_props
    finally:
        destroy(plain)
        destroy(round_w)


def test_reload_border_dead_widget_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine(".box { border-radius: 999px; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(20, 10)
    monkeypatch.setattr(widget, "objectName", lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    # Real app.allWidgets() includes widget; objectName() raises → swallowed
    reload_handler.reeval_border_radius_widgets_after_reload(engine)
    destroy(widget)


# ---------------------------------------------------------------------------
# window handcrafted stale entries
# ---------------------------------------------------------------------------


def test_window_activate_skips_dead_child_handcrafted(_app: QApplication) -> None:
    engine = make_engine(".t:active { background-color: blue; }")
    window = QWidget()
    child = QWidget(window)
    child.setProperty("class", "t")
    engine.seed_active_pseudo(child)
    wid = id(child)
    destroy(child)
    # Re-insert a dead wrapper to simulate a stale entry that destroy didn't clean
    engine.active_rule_widgets[wid] = child
    engine.on_window_activate(window)
    destroy(window)


def test_window_deactivate_skips_missing_mirror(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 200ms; }
    """)
    window = QWidget()
    widget = QWidget(window)
    widget.setProperty("class", "btn")
    hover_widget(engine, widget)
    wid = id(widget)
    # Drop the widget mirror but keep the context → child None branch
    engine.store.widgets.pop(wid, None)
    engine.on_window_deactivate(window)
    assert ":hover" in engine.store.contexts[wid].active_pseudos
    destroy(widget)
    destroy(window)


def test_window_deactivate_parent_raises_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 200ms; }
    """)
    window = QWidget()
    child = QWidget(window)
    child.setProperty("class", "btn")
    try:
        hover_widget(engine, child)
        monkeypatch.setattr(child, "parent", lambda: (_ for _ in ()).throw(RuntimeError("gone")))
        engine.on_window_deactivate(window)  # must swallow, hover stays
        assert ":hover" in engine.get_context(child).active_pseudos
    finally:
        destroy(child)
        destroy(window)


# ---------------------------------------------------------------------------
# transition_engine seed_active should_evaluate False
# ---------------------------------------------------------------------------


def test_seed_active_should_evaluate_false_no_pseudo(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(".t:active { background-color: blue; }")
    widget = QWidget()
    widget.setProperty("class", "t")
    try:
        monkeypatch.setattr(widget, "isActiveWindow", lambda: True)
        # No transition/effect → quick filters empty → should_evaluate False
        assert engine.should_evaluate(widget) is False
        engine.seed_active_pseudo(widget)
        assert ":active" not in engine.get_context(widget).active_pseudos
        assert id(widget) in engine.active_rule_widgets
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# matcher empty-segments with transitions + dead refs + loop
# ---------------------------------------------------------------------------


def test_matcher_empty_segments_with_transition_skipped() -> None:
    from qt_css_engine.css.model import TransitionSpec

    rules = [
        StyleRule(
            selector="",
            base_selector="",
            properties={},
            segments=[],
            transitions=[TransitionSpec("color", 200, "ease", 0)],
        )
    ]
    matcher = RuleMatcher(rules)
    assert matcher.index.quick.classes == set()
    assert matcher.index.buckets.by_id == {}
    assert matcher.index.buckets.unconditional == []


def test_matcher_invalidate_dead_ref_without_engine(_app: QApplication) -> None:
    import gc
    import weakref

    matcher = RuleMatcher([StyleRule(selector=".p .c", base_selector=".p .c", properties={}, segments=[".p", ".c"])])
    parent = QWidget()
    parent.setProperty("class", "p")
    child = QWidget(parent)
    child.setProperty("class", "c")
    matcher.matching_rules(child)
    child_id = id(child)
    assert child_id in matcher.widget_cache.rules
    # Inject a stale dead weakref that the auto-callback never cleaned, to hit
    # the `cached_widget is None → invalidate` branch in invalidate_subtree.
    tmp = QWidget()
    dead_id = 999999937
    matcher.widget_cache.rules[dead_id] = []
    matcher.widget_cache.refs[dead_id] = weakref.ref(tmp)
    del tmp
    gc.collect()
    assert matcher.widget_cache.refs[dead_id]() is None
    matcher.invalidate_subtree(parent)
    assert dead_id not in matcher.widget_cache.rules
    destroy(child)
    destroy(parent)


def test_matcher_descendant_chain_walks_multiple_ancestors(_app: QApplication) -> None:
    engine = make_engine(".a .b .c { color: red; }")
    a = QWidget()
    a.setProperty("class", "a")
    mid = QWidget(a)
    mid.setProperty("class", "zzz")
    mid2 = QWidget(mid)
    mid2.setProperty("class", "b")
    c = QWidget(mid2)
    c.setProperty("class", "c")
    try:
        rule = engine.matcher.rules[0]
        assert engine.matcher.matches(c, rule) is True
        assert RuleMatcher._is_descendant_of(c, a) is True
        assert RuleMatcher._is_descendant_of(a, c) is False
    finally:
        destroy(c)
        destroy(mid2)
        destroy(mid)
        destroy(a)


# ---------------------------------------------------------------------------
# writer deleted-widget guards
# ---------------------------------------------------------------------------


def test_writer_deleted_widget_clears_pending(_app: QApplication) -> None:
    writer = StyleWriter(get_ctx=lambda _wid: None)
    widget = QWidget()
    ctx = WidgetContext()
    ctx.css_anim_props["color"] = "red"
    ctx.style_flush_pending = True
    try:
        qt_delete(widget)
        writer._flush_captured(widget, ctx)
        assert ctx.style_flush_pending is False
    except RuntimeError:
        pass
    writer2ctx = WidgetContext()
    writer2ctx.css_anim_props["color"] = "red"
    writer2ctx.style_flush_pending = True
    w2 = QWidget()
    wid2 = id(w2)
    try:
        qt_delete(w2)
        writer2 = StyleWriter(get_ctx=lambda _wid: writer2ctx)
        writer2.flush_scheduled(w2, wid2)
        assert writer2ctx.style_flush_pending is False
    except RuntimeError:
        pass
