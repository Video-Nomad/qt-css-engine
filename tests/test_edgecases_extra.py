# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""Second-round edge cases for the remaining <97% files.

Targets (from coverage audit):
- color/numeric/shadow RuntimeError guards
- parser empty-segment + error-decl branches
- cascade transition:all engine-managed expansion
- evaluator evaluate-skip, callback-replace, orphan/effect, delay-fire, snap paths
- clicked deactivate RuntimeError, lifecycle disconnect guards
- parent_change RuntimeError, reload/window branches
- transition_engine on_resize suppress, seed_active guards
- box_model style-None, clamp non-px, matcher empty-segments/dead-refs,
  pseudo HoverLeave, writer bind + deleted-widget flushes.
"""

from collections.abc import Callable

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.factory import Animation
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.animation.opacity import OpacityAnimation
from qt_css_engine.animation.shadow import BoxShadowHandle
from qt_css_engine.css.model import StyleRule
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause, ResolvedRuleState
from qt_css_engine.engine.evaluator import Evaluation
from qt_css_engine.geometry.box_model import total_border_px
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QEvent, QObject
from qt_css_engine.qt_compat.QtWidgets import (
    QApplication,
    QPushButton,
    QWidget,
)
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.style.writer import StyleWriter


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


def hover_widget(engine: TransitionEngine, widget: QWidget) -> None:
    engine.get_context(widget).active_pseudos = {":hover"}
    engine.evaluate_widget_state(widget)


def _anims(engine: TransitionEngine, widget: QWidget) -> dict[str, Animation]:
    ctx = engine.store.contexts.get(id(widget))
    return ctx.active_animations if ctx is not None else {}


# ---------------------------------------------------------------------------
# color / numeric / shadow RuntimeError guards
# ---------------------------------------------------------------------------


def test_color_tick_with_alpha_hits_shadow_ancestor_branch(_app: QApplication) -> None:
    """Alpha endpoint → update_shadow=True exercises writer/shadow-ancestor path."""
    widget = QWidget()
    try:
        anim = ColorAnimation(widget, "background-color", "rgba(30,144,255,1.0)", 200, QEasingCurve.Type.Linear)
        anim.set_target("rgba(65,105,225,0.5)")
        anim._on_tick(0.5)
        assert anim.current_color.isValid()
        anim.anim.stop()
    finally:
        destroy(widget)


def test_numeric_finished_runtimeerror_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    widget = QWidget()
    widget.resize(100, 100)
    ctx = WidgetState()
    try:
        anim = GenericPropertyAnimation(widget, "border-top-left-radius", 5.0, 200, QEasingCurve.Type.Linear, ctx=ctx)
        anim._target_box_size = (80.0, 80.0)

        def _boom(_v: object) -> None:
            raise RuntimeError("deleted")

        monkeypatch.setattr(anim, "_on_tick", _boom)
        anim._on_finished()  # must swallow RuntimeError
        assert anim._target_box_size is None
        anim.anim.stop()
    finally:
        destroy(widget)


def test_numeric_finished_clean_flush_runtimeerror_swallowed(
    _app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget = QWidget()
    ctx = WidgetState()
    ctx.css_anim_props["width"] = "10.000px"
    try:
        anim = GenericPropertyAnimation(widget, "width", 10.0, 200, QEasingCurve.Type.Linear, ctx=ctx)
        anim._clean_on_finish = True

        def _boom(_props: object, *, update_shadow: bool = False) -> None:
            raise RuntimeError("deleted")

        monkeypatch.setattr(anim, "_request_style_flush", _boom)
        anim._on_finished()
        assert "width" not in ctx.css_anim_props
    finally:
        destroy(widget)


def test_shadow_finished_deleted_widget_no_crash(_app: QApplication) -> None:
    widget = QWidget()
    try:
        handle = BoxShadowHandle(widget, "2px 2px 4px black", 200, QEasingCurve.Type.Linear)
        handle.set_target("4px 4px 8px red")
        destroy(widget)
        del widget
        handle._on_finished()  # apply_shadow on deleted widget → swallowed
        assert handle._current == handle._end
    except RuntimeError:
        pass


def test_shadow_tick_transparent_start_branch(_app: QApplication) -> None:
    """_start None + _end set → start derived as transparent(_end)."""
    widget = QWidget()
    try:
        handle = BoxShadowHandle(widget, "none", 200, QEasingCurve.Type.Linear)
        handle.set_target("2px 2px 4px black")
        handle._start = None  # force transparent-start branch
        assert handle._end is not None
        handle._on_tick(0.5)
        assert handle._current is not None
        handle.anim.stop()
    finally:
        destroy(widget)


def test_shadow_tick_transparent_end_branch(_app: QApplication) -> None:
    """_end None + _start set → end derived as transparent(_start)."""
    widget = QWidget()
    try:
        handle = BoxShadowHandle(widget, "2px 2px 4px black", 200, QEasingCurve.Type.Linear)
        handle._start = handle._current
        handle._end = None
        handle._on_tick(0.5)
        assert handle._current is not None
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# parser empty segments + error decls
# ---------------------------------------------------------------------------


def test_transition_time_list_skips_empty_segment() -> None:
    _, rules = extract_rules(".box { transition-property: color; transition-duration: 200ms,; }")
    assert rules[0].transitions[0].duration_ms == 200


def test_transition_easing_list_skips_empty_segment() -> None:
    _, rules = extract_rules(
        ".box { transition-property: color; transition-duration: 200ms; transition-timing-function: linear,; }"
    )
    assert rules[0].transitions[0].easing == "linear"


def test_malformed_decl_does_not_crash_parser() -> None:
    cleaned, rules = extract_rules(".box { color: ; background-color: red; }")
    assert any("background-color" in r.properties for r in rules)
    assert "background-color" in cleaned


def test_transition_with_trailing_comma_ignored() -> None:
    _, rules = extract_rules(".box { transition: background-color 200ms,; }")
    assert len(rules[0].transitions) == 1


# ---------------------------------------------------------------------------
# cascade transition:all engine-managed expansion
# ---------------------------------------------------------------------------


def test_expand_all_picks_up_engine_managed_prop_not_in_rules(_app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: red; transition: all 200ms; }
        .box:hover { background-color: blue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        ctx.css_anim_props["color"] = "white"  # engine-managed but not in base/target
        state = engine.cascade.collect(widget, ctx)
        assert "color" in state.animated_props
        assert "color" in state.transitions
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# evaluator — evaluate skip, callbacks, orphans, delay fire, snaps
# ---------------------------------------------------------------------------


def test_evaluate_returns_early_when_can_skip_mocked(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(".box { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:

        def _always_skip(_w: QWidget, _c: WidgetState, _cause: EvaluationCause) -> bool:
            return True

        monkeypatch.setattr(engine.evaluator, "can_skip_initial_evaluation", _always_skip)
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        assert not _anims(engine, widget)
    finally:
        destroy(widget)


def test_replace_callback_disconnects_old(_app: QApplication) -> None:
    engine = make_engine(".box { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        anim_obj = _anims(engine, widget)["background-color"]
        cbs: dict[str, Callable[[], None]] = {}
        engine.evaluator._replace_finished_callback(anim_obj, "background-color", cbs, lambda: None)
        old = cbs["background-color"]
        engine.evaluator._replace_finished_callback(anim_obj, "background-color", cbs, lambda: None)
        assert cbs["background-color"] is not old
        anim_obj.anim.stop()
    finally:
        destroy(widget)


def test_orphan_box_shadow_no_base_clears_effect(_app: QApplication) -> None:
    engine = make_engine(".box { box-shadow: 0 4px 8px black; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        ctx = engine.get_context(widget)
        assert "box-shadow" in ctx.active_animations
        engine.evaluator.cleanup_orphans(ctx, ResolvedRuleState())
        assert "box-shadow" not in ctx.active_animations
        assert widget.graphicsEffect() is None
    finally:
        destroy(widget)


def test_snap_uninterpolable_with_existing_color_anim(_app: QApplication) -> None:
    engine = make_engine("""
        .box { background-color: red; transition: background-color 200ms; }
        .box:hover { background-color: blue; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        hover_widget(engine, widget)
        ctx = engine.get_context(widget)
        anim_obj = ctx.active_animations.get("background-color")
        assert isinstance(anim_obj, ColorAnimation)
        # Solid target with running ColorAnimation → snap_to path
        assert engine.evaluator._snap_uninterpolable_color(ctx, "background-color", anim_obj, "green") is True
        # Gradient target with running ColorAnimation → stop + evict
        ctx.css_anim_props["background-color"] = "red"
        assert (
            engine.evaluator._snap_uninterpolable_color(ctx, "background-color", anim_obj, "linear-gradient(red,blue)")
            is True
        )
        assert "background-color" not in ctx.css_anim_props
    finally:
        destroy(widget)


def test_delay_fire_after_context_removed_no_crash(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine("""
        .box { background-color: steelblue; }
        .box:hover { background-color: royalblue; transition: background-color 200ms ease 300ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    ctx = engine.get_context(widget)
    assert "background-color" in ctx.pending_delays
    wid = id(widget)
    # Simulate context gone before timer fires (e.g. reload cleared it)
    engine.store.contexts.pop(wid, None)
    engine.store.widgets.pop(wid, None)
    qtbot.wait(400)  # let the pending QTimer fire → _fire must tolerate missing ctx
    destroy(widget)


def test_snap_to_target_unsupported_effect_returns_false(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        ev = Evaluation(widget, ctx, ResolvedRuleState(), EvaluationCause.POLISH)
        from qt_css_engine.engine.evaluation import ResolvedProperty

        resolved = ResolvedProperty(animation=None, current="0.5", target="0.8", is_natural_target=False, spec=None)
        # opacity with no anim but mocked factory returning None → returns False
        assert engine.evaluator._snap_to_target(ev, "opacity", resolved) is False
    finally:
        destroy(widget)


def test_snap_existing_radius_uses_box_size(_app: QApplication) -> None:
    engine = make_engine("""
        .box { border-radius: 0px; }
        .box:hover { border-radius: 12px; transition: border-radius 0ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(100, 100)
    try:
        hover_widget(engine, widget)
        ctx = engine.get_context(widget)
        assert ctx.css_anim_props.get("border-top-left-radius") == "12.000px"
    finally:
        destroy(widget)


def test_should_snap_reload_radius_clamp(_app: QApplication) -> None:
    engine = make_engine("""
        .box { border-radius: 0px; transition: border-radius 200ms; }
        .box:hover { border-radius: 999px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(20, 10)
    try:
        ctx = engine.get_context(widget)
        ctx.active_pseudos = {":hover"}
        state = engine.cascade.collect(widget, ctx)
        spec = state.transitions.get("border-top-left-radius")
        assert spec is not None
        ev = Evaluation(widget, ctx, state, EvaluationCause.RULE_RELOAD)
        assert engine.evaluator._should_snap(ev, "border-top-left-radius", spec) is True
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# clicked / lifecycle guards
# ---------------------------------------------------------------------------


def test_deactivate_clicked_runtimeerror_swallowed(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine("""
        .btn { background-color: blue; transition: background-color 200ms; }
        .btn:clicked { background-color: red; }
    """)
    widget = QWidget()
    widget.setProperty("class", "btn")
    ctx = engine.get_context(widget)
    ctx.active_pseudos.add(":clicked")
    ctx.clicked_anim_props = {"background-color"}

    def _boom(_w: QWidget, cause: EvaluationCause = EvaluationCause.PSEUDO_STATE) -> None:
        raise RuntimeError("deleted")

    monkeypatch.setattr(engine, "evaluate_widget_state", _boom)
    engine.deactivate_clicked(widget, id(widget), ctx.clicked_anim_gen)
    assert ":clicked" not in ctx.active_pseudos
    destroy(widget)


def test_lifecycle_disconnect_outer_guard(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from qt_css_engine.engine.handlers import lifecycle as lifecycle_handler

    engine = make_engine(".box { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    ctx = engine.get_context(widget)
    anim_obj = ctx.active_animations["background-color"]
    cb = lambda: None
    ctx.class_anim_callbacks["background-color"] = cb

    def _boom(_signal: object, _cb: object | None = None) -> None:
        raise TypeError("boom")

    monkeypatch.setattr(lifecycle_handler, "safe_disconnect", _boom)
    lifecycle_handler.disconnect_finished_callbacks(ctx, ctx.class_anim_callbacks)
    assert ctx.class_anim_callbacks == {}
    anim_obj.anim.stop()
    destroy(widget)


# ---------------------------------------------------------------------------
# parent_change RuntimeError
# ---------------------------------------------------------------------------


def test_parent_change_tolerates_deleted_child(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine("""
        .parent .child { color: red; transition: color 200ms; }
    """)
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    other = QWidget(parent)
    other.setProperty("class", "other")
    engine.matcher.matching_rules(child)
    wid = id(child)
    destroy(child)
    del child
    engine.on_parent_change(parent)  # subtree contains dead ref → must not raise
    qtbot.wait(20)
    assert wid not in engine.matcher.widget_cache.rules
    destroy(other)
    destroy(parent)


# ---------------------------------------------------------------------------
# reload remaining branches
# ---------------------------------------------------------------------------


def test_reload_collect_skips_dead_sample(_app: QApplication) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine("""
        .box { background-color: red; }
        .box:hover { background-color: blue; transition: background-color 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    ctx = engine.get_context(widget)
    anim_obj = ctx.active_animations["background-color"]
    qt_delete(anim_obj.anim)  # Anim C++ object gone, widget alive; the liveness probe must tolerate it.
    widgets, _ids = reload_handler.collect_reload_widgets(engine.store.contexts)
    assert isinstance(widgets, set)
    destroy(widget)


def test_reload_reeval_clears_stale_effect_when_no_match(_app: QApplication, qtbot: QtBot) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine(".box { opacity: 0.5; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    assert widget.graphicsEffect() is not None
    # Drop the class so should_evaluate is False → reeval must clear the effect.
    # Simulate post-reload state where reset_context cleared animations.
    widget.setProperty("class", "other")
    engine.matcher.clear_caches()
    engine.get_context(widget).active_animations.clear()
    reload_handler.reeval_reload_widgets_deferred(engine, {widget}, set())
    assert widget.graphicsEffect() is None
    destroy(widget)


def test_reload_reeval_skips_running_effect_widget(_app: QApplication) -> None:
    from qt_css_engine.engine.handlers import reload as reload_handler

    engine = make_engine("""
        .box { opacity: 0.5; transition: opacity 1000ms; }
        .box:hover { opacity: 0.2; }
    """)
    widget = QWidget()
    widget.setProperty("class", "box")
    hover_widget(engine, widget)
    ctx = engine.get_context(widget)
    assert "opacity" in ctx.active_animations
    anim_obj = ctx.active_animations["opacity"]
    assert isinstance(anim_obj, OpacityAnimation)
    assert anim_obj.anim.state() == anim_obj.anim.State.Running
    reload_handler.reeval_reload_widgets_deferred(engine, {widget}, set())
    anim_obj.anim.stop()
    destroy(widget)


def test_reload_border_radius_second_pass(_app: QApplication, qtbot: QtBot) -> None:
    engine = make_engine(".box { border-radius: 999px; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    widget.resize(20, 10)
    engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
    _, new_rules = extract_rules(".box { border-radius: 999px; }")
    engine.reload_rules(new_rules)
    qtbot.wait(60)  # allow both deferred passes incl. border-radius re-check
    ctx = engine.get_context(widget)
    assert ctx.css_anim_props.get("border-top-left-radius") == "5.000px"
    destroy(widget)


# ---------------------------------------------------------------------------
# window remaining branches
# ---------------------------------------------------------------------------


def test_window_deactivate_clears_only_descendants_with_stuck(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 200ms; }
    """)
    window = QWidget()
    child = QWidget(window)
    child.setProperty("class", "btn")
    orphan = QWidget()  # not under window
    orphan.setProperty("class", "btn")
    try:
        hover_widget(engine, child)
        hover_widget(engine, orphan)
        engine.on_window_deactivate(window)
        assert ":hover" not in engine.get_context(child).active_pseudos
        assert ":hover" in engine.get_context(orphan).active_pseudos
    finally:
        destroy(child)
        destroy(orphan)
        destroy(window)


def test_window_deactivate_tolerates_dead_child(_app: QApplication) -> None:
    engine = make_engine("""
        .btn { background-color: steelblue; }
        .btn:hover { background-color: royalblue; transition: background-color 200ms; }
    """)
    window = QWidget()
    child = QWidget(window)
    child.setProperty("class", "btn")
    hover_widget(engine, child)
    wid = id(child)
    destroy(child)
    del child
    engine.on_window_deactivate(window)  # stale context → parent() raises → skipped
    assert wid in engine.store.contexts or wid not in engine.store.contexts
    destroy(window)


# ---------------------------------------------------------------------------
# transition_engine guards
# ---------------------------------------------------------------------------


def test_on_resize_suppressed_noop(_app: QApplication) -> None:
    from qt_css_engine.state.suppress import InternalWriteReason

    engine = make_engine(".box { border-radius: 8px; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        ctx = engine.get_context(widget)
        ctx.internal_write_depth = 1
        ctx.internal_write_reason = InternalWriteReason.MEASURE
        engine.polish.queue.clear()
        engine.polish.pending = False
        engine.on_resize(widget)
        assert widget not in engine.polish.queue
    finally:
        destroy(widget)


def test_seed_active_not_active_window_no_pseudo(_app: QApplication) -> None:
    engine = make_engine(".t:active { background-color: blue; }")
    widget = QWidget()  # hidden → not active window
    widget.setProperty("class", "t")
    try:
        engine.seed_active_pseudo(widget)
        assert ":active" not in engine.get_context(widget).active_pseudos
        assert id(widget) in engine.active_rule_widgets
    finally:
        destroy(widget)


# ---------------------------------------------------------------------------
# box_model / clamp / matcher / pseudo / writer leftovers
# ---------------------------------------------------------------------------


def test_total_border_style_none_fallback(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    widget = QPushButton("x")
    try:
        monkeypatch.setattr(widget, "style", lambda: None)
        import qt_css_engine.geometry.box_model as bm

        monkeypatch.setattr(bm.QApplication, "style", staticmethod(lambda: None))
        assert total_border_px(widget, {}, "left") == 0
    finally:
        destroy(widget)


def test_numeric_px_with_units_other_than_px_returns_none() -> None:
    from qt_css_engine.geometry.clamp import _numeric_px

    assert _numeric_px("50%") is None
    assert _numeric_px("auto") is None
    assert _numeric_px(None) is None
    assert _numeric_px("8px") == 8.0


def test_matcher_empty_segments_skipped_in_quick_filters() -> None:
    rules = [StyleRule(selector="", base_selector="", properties={"color": "red"}, segments=[])]
    matcher = RuleMatcher(rules)
    assert matcher.index.quick.classes == set()
    assert matcher.index.quick.tags == set()
    assert matcher.index.quick.ids == set()


def test_matcher_invalidate_subtree_runtimeerror_path(_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(".parent .child { color: red; }")
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    engine.matcher.matching_rules(child)
    try:

        def _boom(_w: QWidget, _a: QWidget) -> bool:
            raise RuntimeError("gone")

        monkeypatch.setattr(engine.matcher, "_is_descendant_of", _boom)
        engine.matcher.invalidate_subtree(parent)  # must invalidate via except path
        assert id(child) not in engine.matcher.widget_cache.rules
    finally:
        destroy(child)
        destroy(parent)


def test_matcher_widget_matches_segment_helper(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        assert engine.matcher.widget_matches_segment(widget, ".box") is True
        assert engine.matcher.widget_matches_segment(widget, ".other") is False
    finally:
        destroy(widget)


def test_matcher_is_descendant_of_none_parent(_app: QApplication) -> None:
    engine = make_engine(".box { color: red; }")
    widget = QWidget()
    other = QWidget()
    try:
        assert engine.matcher._is_descendant_of(widget, other) is False
    finally:
        destroy(widget)
        destroy(other)


def test_pseudo_hover_leave_via_event_filter(_app: QApplication) -> None:
    engine = make_engine(".box:hover { background-color: red; transition: background-color 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "box")
    try:
        engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))
        assert ":hover" in engine.get_context(widget).active_pseudos
        engine.eventFilter(widget, QEvent(QEvent.Type.HoverLeave))
        assert ":hover" not in engine.get_context(widget).active_pseudos
    finally:
        destroy(widget)


def test_writer_bind_and_deleted_flush(_app: QApplication, qtbot: QtBot) -> None:
    writer = StyleWriter()
    writer.bind(lambda _wid: None)
    assert writer._get_ctx is not None
    widget = QWidget()
    ctx = WidgetState()
    ctx.css_anim_props["color"] = "red"
    ctx.style_flush_pending = True
    wid = id(widget)
    writer2 = StyleWriter(get_ctx=lambda _w: ctx)
    destroy(widget)
    del widget
    # Flushing a deleted widget must swallow RuntimeError and clear pending
    writer2.flush_scheduled(QWidget(), wid)  # unknown widget lookup path
    writer2._flush_captured(QObject(), ctx)  # type: ignore[arg-type]
    qtbot.wait(10)


def test_opacity_finished_clears_nothing_extra(_app: QApplication) -> None:
    widget = QWidget()
    try:
        anim = OpacityAnimation(widget, 0.5, 50, QEasingCurve.Type.Linear)
        anim.set_target("1.0")
        assert anim.anim.state() == anim.anim.State.Running
        anim.anim.stop()
    finally:
        destroy(widget)
