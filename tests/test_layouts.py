from pytestqt.qtbot import QtBot

from qt_css_engine import TransitionEngine
from qt_css_engine.css_parser import extract_rules
from qt_css_engine.qt_compat.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton

CSS = """
QWidget { font-size: 13px; }
.btn {
    padding: 1px 4px;
    border: 1px solid transparent;
    margin-left: 1px;
    transition: width 300ms linear;
}
.btn.wide {
    width: 50px;
}
"""


def test_nested_layout_size_cache_invalidation(qtbot: QtBot) -> None:
    """
    Regression test for nested layout size hint caching issues.
    If QLayout.invalidate() is called without clearing the QWidget sizeHint cache
    via updateGeometry(), a nested layout with a stretch factor will maintain an
    inflated size after an inline size constraint is removed, causing the CSS engine
    to measure an incorrect natural width.
    """
    _, rules = extract_rules(CSS)
    engine = TransitionEngine(rules, startup_delay_ms=0)

    # 1. Setup the exact nested layout structure that reproduces the bug
    bar_frame = QFrame()
    qtbot.addWidget(bar_frame)
    grid = QGridLayout(bar_frame)
    grid.setContentsMargins(0, 0, 0, 0)

    left_container = QFrame()
    layout = QHBoxLayout(left_container)

    ws_container = QFrame()
    ws_layout = QHBoxLayout(ws_container)
    ws_layout.setContentsMargins(0, 0, 0, 0)
    ws_layout.setSpacing(0)

    target_btn = QPushButton("2")
    target_btn.setProperty("class", "btn wide")

    for i in range(7):
        if i == 1:
            ws_layout.addWidget(target_btn)
        else:
            b = QPushButton(str(i + 1))
            b.setProperty("class", "btn")
            ws_layout.addWidget(b)

    layout.addWidget(ws_container, 0)
    layout.addStretch(1)

    grid.addWidget(left_container, 0, 0)
    bar_frame.resize(2544, 60)

    # Apply the initial CSS rules
    bar_frame.setStyleSheet(CSS)

    from qt_css_engine.qt_compat.QtWidgets import QApplication

    app = QApplication.instance()
    assert app is not None
    app.installEventFilter(engine)

    from qt_css_engine.qt_compat.QtCore import Qt

    bar_frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    bar_frame.show()
    qtbot.wait(100)

    # 2. Assert initial inflated state
    # target_btn has "width: 50px". With Qt's box model calculation it ends up around 62px.
    assert target_btn.width() >= 60

    # 3. Trigger the class change to remove the explicit width constraint.
    # The engine will intercept the DynamicPropertyChange event.
    target_btn.setProperty("class", "btn")
    qtbot.wait(100)

    # 4. Verify the animation target size.
    # If the layout cache bug is present, the layout will distribute leftover space
    # and the engine will mistakenly calculate the target width as ~13px or 24px.
    # If fixed, it should correctly calculate the unconstrained natural width (7px).
    ctx = engine._ctx(target_btn)  # pyright: ignore[reportPrivateUsage]
    assert "width" in ctx.active_animations

    anim_obj = ctx.active_animations["width"]
    # The actual natural size (content box) of a "18px" physical button is 7.0px
    target_val = anim_obj.anim.endValue()
    assert target_val == 7.0, f"Expected target 7.0px, got {target_val}px. Cache invalidation failed."

    app.removeEventFilter(engine)
