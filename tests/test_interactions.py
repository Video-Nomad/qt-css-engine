# pyright: reportPrivateUsage=false
from pytestqt.qtbot import QtBot

from qt_css_engine.handlers import BoxShadowHandle
from qt_css_engine.qt_compat.QtCore import QEasingCurve
from qt_css_engine.qt_compat.QtWidgets import QWidget


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
