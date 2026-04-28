# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false
"""
Coverage tests for size/layout interaction in engine.py.

Targets the trickiest parts of the size pipeline that the larger refactor must preserve:

- _get_natural_size: ancestor-chain activation, write-depth marker, stylesheet strip/restore,
  no-parent-layout fallback, updatesEnabled toggle.
- _resolve_target_raw: 'auto' + natural_hint, size-prop default, color default, empty fallthrough,
  is_natural_target flag.
- _resolve_current_raw: pre_polish_size snapshot, content_box_px conversion, css_anim_props
  precedence, base_raw fallback.
- _apply_prop_animation: frozen-prop pre-freeze for size, natural-target early-return paths,
  post clean_on_finish reentry.
- _cleanup_orphans: snap_to_natural for size props, snap_to for non-size.
- content_box_px / get_preferred_size_fallback: QFrame vs non-QFrame, zero clamp.
- Class-change return-trip with size: natural_hint suppression, clean_on_finish path.
"""

import pytest
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.css_parser import extract_rules
from qt_css_engine.handlers import GenericPropertyAnimation
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QEasingCurve, Qt
from qt_css_engine.qt_compat.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qt_css_engine.types import EvaluationCause, InternalWriteReason
from qt_css_engine.utils import content_box_px, get_preferred_size_fallback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def destroy(widget: QWidget) -> None:
    qt_delete(widget)


# ---------------------------------------------------------------------------
# _get_natural_size — no constraint / no parent layout
# ---------------------------------------------------------------------------


def test_natural_size_no_constraint_returns_preferred_fallback(_app: QApplication) -> None:
    """No size prop is in css_anim_props → fast path returns get_preferred_size_fallback."""
    engine = make_engine(".x { width: 100px; }")
    widget = QWidget()
    widget.setProperty("class", "x")

    base_props = {"width": "100px"}
    expected = get_preferred_size_fallback(widget, base_props, "width")
    result = engine._get_natural_size(widget, base_props, "width")

    assert result == expected
    destroy(widget)


def test_natural_size_no_parent_layout_falls_back_to_preferred(_app: QApplication) -> None:
    """Constrained widget with no parent layout falls back to sizeHint-based measurement."""
    engine = make_engine(".x { width: 100px; }")
    widget = QWidget()  # orphan — no parent
    widget.setProperty("class", "x")

    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "100px"  # mark constrained

    result = engine._get_natural_size(widget, {}, "width")
    expected = get_preferred_size_fallback(widget, {}, "width")
    assert result == expected
    destroy(widget)


def test_natural_size_internal_write_marker_set_during_measure(_app: QApplication) -> None:
    """During measure, internal_write_depth > 0 and reason == MEASURE."""
    engine = make_engine(".x { width: 100px; }")
    parent = QWidget()
    layout = QHBoxLayout(parent)
    widget = QWidget(parent)
    layout.addWidget(widget)

    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "100px"

    captured_depth: list[int] = []
    captured_reason: list[InternalWriteReason | None] = []

    orig_set = widget.setStyleSheet

    def spy(css: str | None) -> None:
        captured_depth.append(ctx.internal_write_depth)
        captured_reason.append(ctx.internal_write_reason)
        orig_set(css)

    widget.setStyleSheet = spy  # type: ignore[method-assign]
    engine._get_natural_size(widget, {}, "width")

    assert any(d >= 1 for d in captured_depth), "internal_write_depth must be incremented during measure"
    assert InternalWriteReason.MEASURE in captured_reason
    assert ctx.internal_write_depth == 0
    assert ctx.internal_write_reason is None
    destroy(parent)


def test_natural_size_restores_inline_stylesheet_after_measure(_app: QApplication) -> None:
    """css_anim_props are restored to the inline stylesheet after measurement completes."""
    engine = make_engine(".x { width: 100px; }")
    parent = QWidget()
    layout = QHBoxLayout(parent)
    widget = QPushButton("hi", parent)
    layout.addWidget(widget)
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    parent.show()
    parent.resize(400, 50)

    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "200px"
    widget.setStyleSheet(f"{type(widget).__name__}[_anim_scope] {{ width: 200px; }}")

    engine._get_natural_size(widget, {}, "width")

    assert "200px" in widget.styleSheet()
    destroy(parent)


def test_natural_size_disables_window_updates_during_measure(_app: QApplication) -> None:
    """updatesEnabled is toggled off during measure and restored afterwards."""
    engine = make_engine(".x { width: 100px; }")
    parent = QWidget()
    layout = QHBoxLayout(parent)
    widget = QPushButton("hi", parent)
    layout.addWidget(widget)

    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "100px"

    captured_during: list[bool] = []
    orig_set = widget.setStyleSheet

    def spy(css: str | None) -> None:
        win = parent.window()
        if win is not None:
            captured_during.append(win.updatesEnabled())
        orig_set(css)

    widget.setStyleSheet = spy  # type: ignore[method-assign]
    assert parent.updatesEnabled()
    engine._get_natural_size(widget, {}, "width")

    assert captured_during and captured_during[0] is False, "window updates must be off during measure"
    assert parent.updatesEnabled() is True, "updates must be re-enabled afterward"
    destroy(parent)


def test_natural_size_height_axis_uses_height_constraint(_app: QApplication) -> None:
    """A height-axis prop must look at height/min-height/max-height in css_anim_props."""
    engine = make_engine(".x { height: 50px; }")
    parent = QWidget()
    layout = QVBoxLayout(parent)
    widget = QPushButton("hi", parent)
    layout.addWidget(widget)

    ctx = engine._ctx(widget)
    ctx.css_anim_props["min-height"] = "50px"

    result_w = engine._get_natural_size(widget, {}, "width")
    assert result_w == get_preferred_size_fallback(widget, {}, "width")
    destroy(parent)


def test_natural_size_axis_props_handles_min_max_alongside_width(_app: QApplication) -> None:
    """With min-width and max-width in css_anim_props, both are stripped during measure."""
    engine = make_engine(".x { min-width: 200px; max-width: 200px; }")
    parent = QWidget()
    layout = QHBoxLayout(parent)
    widget = QPushButton("hi", parent)
    layout.addWidget(widget)

    ctx = engine._ctx(widget)
    ctx.css_anim_props["min-width"] = "200px"
    ctx.css_anim_props["max-width"] = "200px"

    captured_first: list[str] = []
    orig_set = widget.setStyleSheet

    def spy(css: str | None) -> None:
        if not captured_first:
            captured_first.append(css or "")
        orig_set(css)

    widget.setStyleSheet = spy  # type: ignore[method-assign]
    engine._get_natural_size(widget, {}, "width")

    first = captured_first[0]
    assert "min-width" not in first and "max-width" not in first, (
        f"min-width/max-width must be stripped during measure, got: {first}"
    )
    destroy(parent)


# ---------------------------------------------------------------------------
# _get_natural_size — ancestor chain activation order (the test_layouts root cause)
# ---------------------------------------------------------------------------


def test_natural_size_outer_first_activation_collects_chain(_app: QApplication, qtbot: QtBot) -> None:
    """
    Reproduces the layouts.py bug at unit level: nested layout with stretch.
    Without outermost-first activation, sibling redistribution fills the stale wide frame.
    With the fix, natural width matches sizeHint of an unconstrained button.
    """
    engine = make_engine(".btn.wide { width: 200px; }")
    bar = QFrame()
    qtbot.addWidget(bar)
    outer = QHBoxLayout(bar)
    outer.setContentsMargins(0, 0, 0, 0)

    inner_host = QFrame(bar)
    inner = QHBoxLayout(inner_host)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.setSpacing(0)

    target = QPushButton("X", inner_host)
    sibling = QPushButton("Y", inner_host)
    inner.addWidget(target)
    inner.addWidget(sibling)

    outer.addWidget(inner_host, 0)
    outer.addStretch(1)

    bar.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    bar.resize(800, 40)
    bar.show()
    qtbot.wait(50)

    ctx = engine._ctx(target)
    ctx.css_anim_props["width"] = "200px"
    target.setStyleSheet("QPushButton[_anim_scope] { width: 200px; }")
    target.setProperty("_anim_scope", "test")

    result = engine._get_natural_size(target, {}, "width")
    # Must be a small content-box value (~ button text width), nowhere near 200px.
    px = int(result.replace("px", ""))
    assert px < 100, f"natural width should be small (sizeHint-bounded), got {px}px"
    destroy(bar)


# ---------------------------------------------------------------------------
# _resolve_target_raw
# ---------------------------------------------------------------------------


def test_resolve_target_auto_with_natural_hint_uses_hint(_app: QApplication) -> None:
    """When target_raw == 'auto' and natural_hint provided, hint is returned (no measure)."""
    engine = make_engine(".x { width: auto; }")
    widget = QWidget()

    target_raw, is_natural = engine._resolve_target_raw(
        widget, base_props={}, target_props={"width": "auto"}, prop="width", natural_hint="42px"
    )
    assert target_raw == "42px"
    assert is_natural is True
    destroy(widget)


def test_resolve_target_auto_no_hint_calls_natural_size(_app: QApplication) -> None:
    """'auto' without hint calls _get_natural_size (returns sizeHint fallback for orphan widget)."""
    engine = make_engine(".x { width: auto; }")
    widget = QWidget()

    target_raw, is_natural = engine._resolve_target_raw(
        widget, base_props={}, target_props={"width": "auto"}, prop="width"
    )
    assert target_raw.endswith("px")
    assert is_natural is True
    destroy(widget)


def test_resolve_target_size_prop_no_value_is_natural(_app: QApplication) -> None:
    """SIZE_PROP with no value in either base or target → natural target."""
    engine = make_engine(".x { color: red; }")
    widget = QWidget()

    target_raw, is_natural = engine._resolve_target_raw(widget, base_props={}, target_props={}, prop="width")
    assert is_natural is True
    assert target_raw.endswith("px")
    destroy(widget)


def test_resolve_target_size_prop_explicit_value_not_natural(_app: QApplication) -> None:
    """Explicit width value → not a natural target."""
    engine = make_engine(".x { width: 50px; }")
    widget = QWidget()

    target_raw, is_natural = engine._resolve_target_raw(
        widget, base_props={"width": "50px"}, target_props={}, prop="width"
    )
    assert target_raw == "50px"
    assert is_natural is False
    destroy(widget)


def test_resolve_target_color_prop_default_white(_app: QApplication) -> None:
    """color prop with no value defaults to 'white'."""
    engine = make_engine(".x { background-color: red; }")
    widget = QWidget()

    target_raw, is_natural = engine._resolve_target_raw(widget, base_props={}, target_props={}, prop="color")
    assert target_raw == "white"
    assert is_natural is False
    destroy(widget)


def test_resolve_target_other_color_default_transparent(_app: QApplication) -> None:
    """non-'color' color props (background-color, border-color) default to 'transparent'."""
    engine = make_engine(".x { color: red; }")
    widget = QWidget()

    target_raw, _ = engine._resolve_target_raw(widget, base_props={}, target_props={}, prop="background-color")
    assert target_raw == "transparent"
    destroy(widget)


def test_resolve_target_non_animatable_no_value_returns_empty(_app: QApplication) -> None:
    """Non-size, non-color prop with no value → empty string (caller short-circuits)."""
    engine = make_engine(".x { color: red; }")
    widget = QWidget()

    target_raw, is_natural = engine._resolve_target_raw(widget, base_props={}, target_props={}, prop="font-weight")
    assert target_raw == ""
    assert is_natural is False
    destroy(widget)


# ---------------------------------------------------------------------------
# _resolve_current_raw
# ---------------------------------------------------------------------------


def test_resolve_current_uses_existing_css_anim_props_value(_app: QApplication) -> None:
    """If css_anim_props has a value for prop, that wins."""
    engine = make_engine(".x { width: 50px; }")
    widget = QWidget()
    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "77.500px"

    result = engine._resolve_current_raw(widget, ctx, "width", base_props={}, base_raw="50px")
    assert result == "77.500px"
    destroy(widget)


def test_resolve_current_pre_polish_size_used_when_set(_app: QApplication) -> None:
    """During class-change (pre_polish_size set), the snapshot beats widget.width()."""
    engine = make_engine(".x { width: 50px; }")
    widget = QWidget()
    widget.resize(300, 100)  # actual width 300
    ctx = engine._ctx(widget)
    ctx.pre_polish_size = (123, 50)

    # Pin border to zero so content_box_px doesn't subtract platform frame width.
    base_props = {"border-left-width": "0px", "border-right-width": "0px"}
    result = engine._resolve_current_raw(widget, ctx, "width", base_props=base_props, base_raw="50px")
    assert result == "123px", f"pre_polish snapshot must beat widget.width(): got {result}"
    destroy(widget)


def test_resolve_current_widget_width_used_when_no_pre_polish(_app: QApplication) -> None:
    """No pre_polish_size and no css_anim_props value → uses live widget.width()."""
    engine = make_engine(".x { width: 50px; }")
    widget = QWidget()
    widget.resize(80, 30)
    ctx = engine._ctx(widget)
    ctx.pre_polish_size = None

    base_props = {"border-left-width": "0px", "border-right-width": "0px"}
    result = engine._resolve_current_raw(widget, ctx, "width", base_props=base_props, base_raw="50px")
    assert result == "80px"
    destroy(widget)


def test_resolve_current_non_size_falls_back_to_base_raw(_app: QApplication) -> None:
    """Non-size prop with no css_anim_props entry → returns base_raw."""
    engine = make_engine(".x { color: red; }")
    widget = QWidget()
    ctx = engine._ctx(widget)

    result = engine._resolve_current_raw(widget, ctx, "color", base_props={}, base_raw="#ff0000")
    assert result == "#ff0000"
    destroy(widget)


def test_resolve_current_height_axis_uses_pre_polish_height(_app: QApplication) -> None:
    """Height prop reads index 1 of pre_polish_size, not index 0."""
    engine = make_engine(".x { height: 50px; }")
    widget = QWidget()
    ctx = engine._ctx(widget)
    ctx.pre_polish_size = (300, 77)

    base_props = {"border-top-width": "0px", "border-bottom-width": "0px"}
    result = engine._resolve_current_raw(widget, ctx, "height", base_props=base_props, base_raw="50px")
    assert result == "77px"
    destroy(widget)


# ---------------------------------------------------------------------------
# _apply_prop_animation — frozen-prop logic
# ---------------------------------------------------------------------------


def test_apply_prop_freezes_size_before_measure_when_no_anim(_app: QApplication) -> None:
    """
    When prop is a SIZE_PROP, no animation exists, and prop not in css_anim_props,
    _apply_prop_animation freezes current size into css_anim_props before any measurement.
    The freeze should be removed when target == base value (snap-skip path).
    """
    engine = make_engine("""
        .x { width: 50px; transition: width 200ms; }
    """)
    widget = QWidget()
    widget.setProperty("class", "x")
    widget.resize(50, 30)
    ctx = engine._ctx(widget)

    # Re-evaluate with no pseudo state and no class-change.
    # Target == base width == 50px. With no anim and no inline value, snap-skip path runs
    # and the frozen prop is removed before return.
    engine._apply_prop_animation(
        widget,
        ctx,
        "width",
        base_props={"width": "50px"},
        target_props={"width": "50px"},
        target_transitions={},
        cause=EvaluationCause.PSEUDO_STATE,
    )

    assert "width" not in ctx.css_anim_props, "frozen prop must be cleaned up on snap-skip"
    destroy(widget)


def test_apply_prop_natural_target_no_anim_returns_false(_app: QApplication) -> None:
    """
    Natural target + no running animation + no pre_polish_size + nothing in css_anim_props:
    Qt lays out at natural size without engine intervention. Must return False (no style update).
    """
    engine = make_engine(".x { width: auto; transition: width 200ms; }")
    widget = QWidget()
    widget.setProperty("class", "x")
    ctx = engine._ctx(widget)

    needs_update = engine._apply_prop_animation(
        widget,
        ctx,
        "width",
        base_props={},
        target_props={},
        target_transitions={},
        cause=EvaluationCause.PSEUDO_STATE,
    )
    assert needs_update is False
    assert "width" not in ctx.css_anim_props
    destroy(widget)


def test_apply_prop_post_clean_on_finish_skips_restart(_app: QApplication) -> None:
    """
    After clean_on_finish removed the prop from css_anim_props (animation completed),
    re-evaluation with natural target + existing anim_obj must return False (no restart).
    Otherwise stretch-fill widgets would animate back to sizeHint instead of staying at full layout width.
    """
    engine = make_engine("""
        .x { transition: width 200ms; }
        .x.wide { width: 200px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "x wide")
    widget.resize(200, 30)
    ctx = engine._ctx(widget)

    # Seed an anim obj as if class change ran already and clean_on_finish has fired.
    anim = GenericPropertyAnimation(widget, "width", 200.0, 200, QEasingCurve.Type.Linear, parent=engine, ctx=ctx)
    ctx.active_animations["width"] = anim
    # GenericPropertyAnimation.__init__ writes via _on_tick — clear AFTER construction
    # to simulate the post-clean_on_finish state.
    ctx.css_anim_props.clear()

    needs_update = engine._apply_prop_animation(
        widget,
        ctx,
        "width",
        base_props={},  # natural target — no width in base/target
        target_props={},
        target_transitions={},
        cause=EvaluationCause.CLASS_ANIMATION_FINISH,
    )
    assert needs_update is False, "post-clean_on_finish must not restart animation toward sizeHint"
    destroy(widget)


# ---------------------------------------------------------------------------
# _cleanup_orphans — size vs non-size paths
# ---------------------------------------------------------------------------


def test_cleanup_orphan_size_prop_uses_snap_to_natural(_app: QApplication) -> None:
    """
    Size prop orphaned (no longer in any rule, no base value) must call snap_to_natural —
    removes the inline constraint so widget returns to layout-assigned size.
    """
    engine = make_engine(".x { transition: width 200ms; }")
    widget = QWidget()
    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "100.000px"
    anim = GenericPropertyAnimation(widget, "width", 100.0, 200, QEasingCurve.Type.Linear, parent=engine, ctx=ctx)
    ctx.active_animations["width"] = anim

    needs_update = engine._cleanup_orphans(widget, ctx, all_animated_props=set(), base_props={})

    assert "width" not in ctx.css_anim_props, "snap_to_natural must remove inline constraint"
    assert "width" not in ctx.active_animations, "orphan animation must be removed from registry"
    assert needs_update is True
    destroy(widget)


def test_cleanup_orphan_size_prop_with_base_value_snaps_to_value(_app: QApplication) -> None:
    """When base_props has a value for a size prop, orphan snaps to that explicit value."""
    engine = make_engine(".x { width: 50px; }")
    widget = QWidget()
    ctx = engine._ctx(widget)
    ctx.css_anim_props["width"] = "100.000px"
    anim = GenericPropertyAnimation(widget, "width", 100.0, 200, QEasingCurve.Type.Linear, parent=engine, ctx=ctx)
    ctx.active_animations["width"] = anim

    needs_update = engine._cleanup_orphans(widget, ctx, all_animated_props=set(), base_props={"width": "50px"})

    assert ctx.css_anim_props.get("width", "").startswith("50"), "orphan must snap to explicit base value, not natural"
    assert needs_update is True
    destroy(widget)


def test_cleanup_stale_snapped_props_evicted(_app: QApplication) -> None:
    """css_anim_props entries with no backing animation and no rule coverage must be evicted."""
    engine = make_engine(".x { color: red; }")
    widget = QWidget()
    ctx = engine._ctx(widget)
    ctx.css_anim_props["min-width"] = "100.000px"  # stale snap, no animation, no rule

    needs_update = engine._cleanup_orphans(widget, ctx, all_animated_props=set(), base_props={})
    assert "min-width" not in ctx.css_anim_props
    assert needs_update is True
    destroy(widget)


# ---------------------------------------------------------------------------
# content_box_px / get_preferred_size_fallback
# ---------------------------------------------------------------------------


def test_content_box_qframe_uses_contents_rect_delta(_app: QApplication) -> None:
    """For QFrame-derived widgets, content_box_px subtracts widget.width() - contentsRect().width()."""
    label = QLabel("hello")
    label.setFrameStyle(QFrame.Shape.Box.value | QFrame.Shadow.Plain.value)
    label.setLineWidth(2)
    label.resize(100, 30)
    label.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    label.show()

    raw = label.width()
    actual = content_box_px(label, {}, "width", raw)
    cr = label.contentsRect()
    expected = raw - max(0, raw - cr.width())
    assert actual == expected
    destroy(label)


def test_content_box_non_qframe_uses_base_props(_app: QApplication) -> None:
    """For non-QFrame widgets, content_box_px subtracts base_props padding+margin+border."""
    btn = QPushButton("hi")
    base_props = {
        "padding-left": "5px",
        "padding-right": "5px",
        "margin-left": "2px",
        "margin-right": "2px",
        "border-left-width": "1px",
        "border-right-width": "1px",
    }
    actual = content_box_px(btn, base_props, "width", 100)
    # 100 - (1+1) - (5+5) - (2+2) = 84
    assert actual == 100 - 2 - 10 - 4
    destroy(btn)


def test_content_box_height_axis_uses_top_bottom(_app: QApplication) -> None:
    """Height-axis content_box_px uses top/bottom triplets."""
    btn = QPushButton("hi")
    # Pin border to zero — non-QFrame widgets otherwise add PM_DefaultFrameWidth.
    base_props = {
        "padding-top": "3px",
        "padding-bottom": "3px",
        "border-top-width": "0px",
        "border-bottom-width": "0px",
    }
    actual = content_box_px(btn, base_props, "height", 50)
    assert actual == 50 - 0 - 6 - 0
    destroy(btn)


def test_get_preferred_size_fallback_clamps_zero(_app: QApplication) -> None:
    """get_preferred_size_fallback never returns negative — extras > sizeHint clamps to 0."""
    btn = QPushButton("x")
    huge_pad = {
        "padding-left": "999px",
        "padding-right": "999px",
        "border-left-width": "999px",
        "border-right-width": "999px",
    }
    result = get_preferred_size_fallback(btn, huge_pad, "width")
    px = int(result.replace("px", ""))
    assert px >= 0
    destroy(btn)


# ---------------------------------------------------------------------------
# Class-change + size: natural_hint suppression and clean_on_finish
# ---------------------------------------------------------------------------


def test_class_change_size_animation_uses_pre_polish_size_as_origin(_app: QApplication, qtbot: QtBot) -> None:
    """
    Class-change away from .wide must animate FROM the inflated width (snapshot via pre_polish_size),
    not from the post-polish 0/sizeHint width. This ensures the user sees a smooth shrink.
    """
    engine = make_engine("""
        .btn { transition: width 300ms; }
        .btn.wide { width: 200px; }
    """)
    parent = QFrame()
    layout = QHBoxLayout(parent)
    btn = QPushButton("hi", parent)
    layout.addWidget(btn)
    btn.setProperty("class", "btn wide")

    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    parent.resize(800, 40)
    parent.show()
    qtbot.wait(50)

    ctx = engine._ctx(btn)
    ctx.css_anim_props["width"] = "200.000px"

    btn.setProperty("class", "btn")
    engine._on_class_change(btn)

    if "width" in ctx.active_animations:
        anim = ctx.active_animations["width"]
        assert isinstance(anim, GenericPropertyAnimation)
        # Start value must reflect pre-polish (constrained) size, not post-polish natural width.
        start = float(anim.anim.startValue())
        assert start >= 100, f"animation must start from inflated width, got {start}"
    destroy(parent)


def test_class_change_to_natural_sets_clean_on_finish(_app: QApplication, qtbot: QtBot) -> None:
    """
    When class-change targets natural size, GenericPropertyAnimation.set_target is called
    with clean_on_finish=True so the inline constraint is removed when the animation ends.
    """
    engine = make_engine("""
        .btn { transition: width 300ms; }
        .btn.wide { width: 200px; }
    """)
    parent = QFrame()
    layout = QHBoxLayout(parent)
    btn = QPushButton("hi", parent)
    layout.addWidget(btn)
    btn.setProperty("class", "btn wide")
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    parent.resize(800, 40)
    parent.show()
    qtbot.wait(50)

    ctx = engine._ctx(btn)
    ctx.css_anim_props["width"] = "200.000px"
    btn.setProperty("class", "btn")
    engine._on_class_change(btn)

    if "width" in ctx.active_animations:
        anim = ctx.active_animations["width"]
        assert isinstance(anim, GenericPropertyAnimation)
        assert anim._clean_on_finish is True, "natural-target class-change must set clean_on_finish"
    destroy(parent)


def test_clean_on_finish_removes_inline_constraint(_app: QApplication, qtbot: QtBot) -> None:
    """When a GenericPropertyAnimation with _clean_on_finish=True ends, css_anim_props loses the prop."""
    widget = QWidget()
    ctx_props: dict[str, str] = {}

    from qt_css_engine.types import WidgetContext

    ctx = WidgetContext()
    ctx.css_anim_props = ctx_props

    anim = GenericPropertyAnimation(widget, "width", 100.0, 30, QEasingCurve.Type.Linear, ctx=ctx)
    anim.set_target("50px", clean_on_finish=True)
    qtbot.wait(80)

    assert anim.anim.state() != QAbstractAnimation.State.Running
    assert "width" not in ctx.css_anim_props, "clean_on_finish must remove prop after animation finishes"
    destroy(widget)


# ---------------------------------------------------------------------------
# End-to-end: hover-driven size animation under nested layout
# ---------------------------------------------------------------------------


def test_hover_size_animation_targets_explicit_value(_app: QApplication, qtbot: QtBot) -> None:
    """Hover on .btn with .btn:hover { width: 80px; } must animate width toward 80."""
    engine = make_engine("""
        .btn { padding: 0; border: 0px solid transparent; transition: width 200ms; }
        .btn:hover { width: 80px; }
    """)
    parent = QFrame()
    layout = QHBoxLayout(parent)
    btn = QPushButton("hi", parent)
    layout.addWidget(btn)
    btn.setProperty("class", "btn")
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    parent.resize(400, 40)
    parent.show()
    qtbot.wait(50)

    ctx = engine._ctx(btn)
    ctx.active_pseudos = {":hover"}
    engine._evaluate_widget_state(btn, cause=EvaluationCause.PSEUDO_STATE)

    assert "width" in ctx.active_animations
    anim = ctx.active_animations["width"]
    assert isinstance(anim, GenericPropertyAnimation)
    assert anim.anim.endValue() == pytest.approx(80.0)
    destroy(parent)


def test_unhover_size_returns_to_natural_clean_on_finish(_app: QApplication, qtbot: QtBot) -> None:
    """Unhover from explicit width back to natural must set clean_on_finish=True on the animation."""
    engine = make_engine("""
        .btn { padding: 0; border: 0px solid transparent; transition: width 200ms; }
        .btn:hover { width: 80px; }
    """)
    parent = QFrame()
    layout = QHBoxLayout(parent)
    btn = QPushButton("hi", parent)
    layout.addWidget(btn)
    btn.setProperty("class", "btn")
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    parent.resize(400, 40)
    parent.show()
    qtbot.wait(50)

    ctx = engine._ctx(btn)
    ctx.active_pseudos = {":hover"}
    engine._evaluate_widget_state(btn, cause=EvaluationCause.PSEUDO_STATE)
    anim = ctx.active_animations["width"]
    assert isinstance(anim, GenericPropertyAnimation)
    # Mid-flight to keep animation alive.
    anim.anim.setCurrentTime(50)

    ctx.active_pseudos = set()
    engine._evaluate_widget_state(btn, cause=EvaluationCause.PSEUDO_STATE)

    assert anim._clean_on_finish is True
    destroy(parent)


# ---------------------------------------------------------------------------
# pre_polish_size lifecycle in _on_class_change
# ---------------------------------------------------------------------------


def test_class_change_clears_pre_polish_size_after_evaluation(_app: QApplication) -> None:
    """_on_class_change must reset ctx.pre_polish_size to None after evaluating."""
    engine = make_engine("""
        .x { transition: width 200ms; }
        .x.wide { width: 100px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "x")
    widget.resize(60, 20)
    ctx = engine._ctx(widget)

    widget.setProperty("class", "x wide")
    engine._on_class_change(widget)

    assert ctx.pre_polish_size is None, "pre_polish_size must be cleared after class-change evaluation"
    destroy(widget)


def test_class_change_snapshots_size_before_polish(_app: QApplication) -> None:
    """_on_class_change captures widget.width()/height() into pre_polish_size before unpolish/polish."""
    engine = make_engine("""
        .x { transition: width 200ms; }
        .x.wide { width: 100px; }
    """)
    widget = QWidget()
    widget.setProperty("class", "x")
    widget.resize(45, 25)

    captured: list[tuple[int, int] | None] = []
    orig_resolve_current_raw = engine._resolve_current_raw

    def spy_resolve(widget_: QWidget, ctx_: object, prop_: str, base_props_: dict[str, str], base_raw_: str) -> str:
        # Snapshot pre_polish_size when resolve_current is invoked during evaluation.
        captured.append(getattr(ctx_, "pre_polish_size", None))
        return orig_resolve_current_raw(widget_, ctx_, prop_, base_props_, base_raw_)  # type: ignore[arg-type]

    engine._resolve_current_raw = spy_resolve  # type: ignore[method-assign]
    widget.setProperty("class", "x wide")
    engine._on_class_change(widget)

    # At least one resolve call must have seen the pre-polish snapshot.
    assert any(snap == (45, 25) for snap in captured), f"expected (45, 25) in {captured}"
    destroy(widget)
