# pyright: reportPrivateUsage=false
from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.css_parser import extract_rules
from qt_css_engine.handlers import BoxShadowHandle, GenericPropertyAnimation
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QEvent, QPointF, Qt
from qt_css_engine.qt_compat.QtGui import QMouseEvent
from qt_css_engine.qt_compat.QtWidgets import QWidget


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def test_box_shadow_rapid_interruption(qtbot: QtBot):
    """
    Regression: rapid hover-enter then hover-leave before first tick must not strand
    the animation heading toward the shadow value.

    Root cause was set_target("none") returning early when target(None)==_current(None),
    even while the animation was running toward a non-None _end.  Fixed by comparing
    against _end (not _current) when the animation is already running.
    """
    widget = QWidget()
    qtbot.addWidget(widget)

    handle = BoxShadowHandle(widget, "none", duration_ms=100, easing_curve=QEasingCurve.Type.Linear)
    assert handle._current is None

    # HoverEnter: start animating toward shadow
    shadow_val = "0px 4px 8px rgba(0, 0, 0, 0.5)"
    handle.set_target(shadow_val)
    assert handle.anim.state() == handle.anim.State.Running
    assert handle._end is not None

    # HoverLeave immediately (before any tick)
    handle.set_target("none")

    # Re-targeting must have taken effect: _end reset to None, still running
    assert handle._end is None, f"Expected _end=None after re-target, got {handle._end}"
    assert handle.anim.state() == handle.anim.State.Running

    # Wait for animation to finish
    qtbot.wait(150)

    assert handle._current is None, f"Shadow should be None, but got {handle._current}"
    assert widget.graphicsEffect() is None


def _right_press() -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(0, 0),
        QPointF(0, 0),
        QPointF(0, 0),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )


_PRESSED_CSS = """
.target   { color: red;   transition: color 200ms; }
.target:pressed   { color: blue; }
.ancestor { color: green; transition: color 200ms; }
.ancestor:pressed { color: yellow; }
"""


def test_right_click_pressed_only_on_target(qtbot: QtBot):
    """Right-click :pressed must apply to the target widget only, not propagate to ancestors."""
    engine = make_engine(_PRESSED_CSS)

    parent = QWidget()
    qtbot.addWidget(parent)
    child = QWidget(parent)
    parent.setProperty("class", "ancestor")
    child.setProperty("class", "target")

    # Same event object simulates Qt's event propagation (child → parent, same QMouseEvent).
    event = _right_press()
    engine.eventFilter(child, event)
    engine.eventFilter(parent, event)

    assert ":pressed" in engine._ctx(child).active_pseudos
    parent_ctx = engine._contexts.get(id(parent))
    assert parent_ctx is None or ":pressed" not in parent_ctx.active_pseudos


def test_left_click_only_ignores_right_click(qtbot: QtBot):
    """With _left_click_only=True, right/middle clicks are fully filtered — no :pressed anywhere."""
    engine = make_engine(_PRESSED_CSS)
    engine._left_click_only = True

    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "target")

    engine.eventFilter(widget, _right_press())

    ctx = engine._contexts.get(id(widget))
    assert ctx is None or ":pressed" not in ctx.active_pseudos


# ---------------------------------------------------------------------------
# :active pseudo-state tests
# ---------------------------------------------------------------------------

_ACTIVE_CSS = """
.target { background-color: red; transition: background-color 200ms; }
.target:active { background-color: blue; }
"""


def test_window_activate_sets_active_pseudo(qtbot: QtBot):
    """WindowActivate event adds :active to matching child widgets."""
    engine = make_engine(_ACTIVE_CSS)

    parent = QWidget()
    qtbot.addWidget(parent)
    child = QWidget(parent)
    child.setProperty("class", "target")
    engine._seed_active_pseudo(child)  # normally triggered at Polish time

    engine.eventFilter(parent, QEvent(QEvent.Type.WindowActivate))

    assert ":active" in engine._ctx(child).active_pseudos


def test_window_deactivate_clears_active_pseudo(qtbot: QtBot):
    """WindowDeactivate clears :active from child widgets."""
    engine = make_engine(_ACTIVE_CSS)

    parent = QWidget()
    qtbot.addWidget(parent)
    child = QWidget(parent)
    child.setProperty("class", "target")
    engine._ctx(child).active_pseudos.add(":active")

    engine.eventFilter(parent, QEvent(QEvent.Type.WindowDeactivate))

    ctx = engine._contexts.get(id(child))
    assert ctx is None or ":active" not in ctx.active_pseudos


def test_leave_window_preserves_active_pseudo(qtbot: QtBot):
    """Mouse leaving the window must NOT clear :active — window is still focused."""
    engine = make_engine(_ACTIVE_CSS)

    parent = QWidget()  # top-level: isWindow() == True
    qtbot.addWidget(parent)
    child = QWidget(parent)
    child.setProperty("class", "target")
    engine._ctx(child).active_pseudos.add(":active")

    engine.eventFilter(parent, QEvent(QEvent.Type.Leave))

    assert ":active" in engine._ctx(child).active_pseudos


def test_window_activate_ignores_non_matching_widgets(qtbot: QtBot):
    """WindowActivate must not set :active on widgets without an :active rule."""
    engine = make_engine(_ACTIVE_CSS)

    parent = QWidget()
    qtbot.addWidget(parent)
    child = QWidget(parent)
    child.setProperty("class", "other")

    engine.eventFilter(parent, QEvent(QEvent.Type.WindowActivate))

    ctx = engine._contexts.get(id(child))
    assert ctx is None or ":active" not in ctx.active_pseudos


def test_active_pseudo_triggers_transition(qtbot: QtBot):
    """:active triggers a background-color animation like any other pseudo-state."""
    engine = make_engine(_ACTIVE_CSS)

    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "target")
    engine._evaluate_widget_state(widget)

    engine._ctx(widget).active_pseudos.add(":active")
    engine._evaluate_widget_state(widget)

    assert "background-color" in engine._ctx(widget).active_animations


def test_border_radius_reclamps_after_resize_event(qtbot: QtBot):
    """
    A radius clamped against an early hidden/pre-layout size must be recomputed once
    the widget receives its final layout size.
    """
    engine = make_engine(".box { border-radius: 99px; }")

    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    widget.resize(20, 20)

    engine.eventFilter(widget, QEvent(QEvent.Type.Polish))
    qtbot.wait(20)

    ctx = engine._ctx(widget)
    radius_props = (
        "border-top-left-radius",
        "border-top-right-radius",
        "border-bottom-right-radius",
        "border-bottom-left-radius",
    )
    assert {ctx.css_anim_props.get(prop) for prop in radius_props} == {"10.000px"}

    widget.resize(200, 40)
    engine.eventFilter(widget, QEvent(QEvent.Type.Resize))
    qtbot.wait(20)

    assert {ctx.css_anim_props.get(prop) for prop in radius_props} == {"20.000px"}


def test_resize_event_does_not_snap_running_size_transition(qtbot: QtBot):
    """
    Resize events emitted by a running size animation must not trigger a polish-style
    re-evaluation, because Polish causes intentionally snap transitions.
    """
    css = """
    .box {
        width: 20px;
        border-radius: 99px;
        transition: width 200ms;
    }
    .box.wide {
        width: 100px;
    }
    """
    engine = make_engine(css)

    widget = QWidget()
    qtbot.addWidget(widget)
    widget.setProperty("class", "box")
    widget.resize(20, 20)
    engine._evaluate_widget_state(widget)

    widget.setProperty("class", "box wide")
    engine._on_class_change(widget)

    ctx = engine._ctx(widget)
    anim = ctx.active_animations.get("width")
    assert isinstance(anim, GenericPropertyAnimation)
    assert anim.anim.state() == anim.anim.State.Running

    engine.eventFilter(widget, QEvent(QEvent.Type.Resize))
    qtbot.wait(20)

    assert anim.anim.state() == anim.anim.State.Running
    assert anim.current_val < float(anim.anim.endValue())


def test_hidden_child_show_reclamps_parent_radius_with_effect_handles(qtbot: QtBot):
    """
    YASB open_meteo shape: a hidden icon label is shown later, resizing its visible
    .widget-container parent. The parent also has opacity/box-shadow handles, which
    must not prevent resize-time border-radius clamping.
    """
    css = """
    .open-meteo-widget .widget-container {
        background-color: red;
        border-radius: 99px;
        border-top-right-radius: 0px;
        border-bottom-right-radius: 0px;
        box-shadow: 0px 0px 10px black;
        opacity: 1.0;
    }
    .open-meteo-widget .icon {
        opacity: 1.0;
    }
    """
    engine = make_engine(css)

    from qt_css_engine.qt_compat.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel

    app = QApplication.instance()
    assert isinstance(app, QApplication)

    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setProperty("class", "open-meteo-widget")
    parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    parent_layout = QHBoxLayout(parent)
    parent_layout.setContentsMargins(0, 0, 0, 0)

    frame = QFrame()
    frame.setProperty("class", "widget-container")
    frame_layout = QHBoxLayout(frame)
    frame_layout.setContentsMargins(0, 0, 0, 0)
    frame_layout.setSpacing(0)
    parent_layout.addWidget(frame)

    label = QLabel("x")
    label.setProperty("class", "icon")
    label.hide()
    frame_layout.addWidget(label)

    app.installEventFilter(engine)
    try:
        parent.show()
        qtbot.wait(20)

        frame_ctx = engine._ctx(frame)
        assert {"box-shadow", "opacity"} <= set(frame_ctx.active_animations)
        assert not any(prop.startswith("border-") and prop.endswith("-radius") for prop in frame_ctx.css_anim_props)

        label.setProperty("class", "icon sunnyDay")
        style = label.style()
        assert style is not None
        style.unpolish(label)
        style.polish(label)
        label.update()
        label.show()
        qtbot.wait(20)

        assert frame.width() > 0
        assert frame_ctx.css_anim_props.get("border-top-left-radius") == "2.000px"
        assert frame_ctx.css_anim_props.get("border-bottom-left-radius") == "2.000px"
        assert "border-top-right-radius" not in frame_ctx.css_anim_props
        assert "border-bottom-right-radius" not in frame_ctx.css_anim_props
    finally:
        app.removeEventFilter(engine)
