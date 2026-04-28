"""
Class-toggle repro for the `.ws-btn.active -> .ws-btn` width snap bug.

Use the "Next WS" button on the right to rotate the active workspace across
buttons 1-7. The script prints pre/post class state, widget widths, inline
engine props, and width-animation metadata so the last-moment snap can be
correlated with the engine state.

This version mirrors the relevant parts of the real Bar setup:
- top-level bar widget
- child `_bar_frame`
- full-width (`100%`) positioning against the current screen
- `QGridLayout` with left/center/right container frames
"""

# pyright: reportPrivateUsage=false

import logging
import os
import sys

from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from qt_css_engine import TransitionEngine, extract_rules

logging.basicConfig(level=logging.DEBUG)
os.environ["CSS_ENGINE_EVENT_LOGGING"] = "1"


REPRO_CSS = """
QWidget { background-color: #1a1a1a; color: white; font-size: 13px; }

.bar { background-color: #1a1a1a; }

.container { background-color: transparent; }

.glazewm-workspaces .ws-btn {
    color: #d8d8d8;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    margin-left: 1px;
    padding: 1px 4px;
    transition: width 300ms linear;
}

.glazewm-workspaces .ws-btn.active_populated,
.glazewm-workspaces .ws-btn.active_empty {
    background: #525252;
}

.glazewm-workspaces .ws-btn.focused_populated,
.glazewm-workspaces .ws-btn.focused_empty {
    width: 50px;
    background: #2d7ef7;
}

.glazewm-workspaces .ws-btn.empty,
.glazewm-workspaces .ws-btn.empty .label {
    color: #9d9d9d;
}

.middle-label {
    background-color: #222;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 6px 12px;
    margin: 0 4px;
}

.right-btn {
    background-color: #2d2d2d;
    color: #cccccc;
    border: 2px solid #3d3d3d;
    border-radius: 4px;
    padding: 6px 14px;
    margin-left: 4px;
    transition: background-color 200ms ease;
}

.right-btn:hover {
    background-color: #aa3333;
}
"""


def make_ws_btn(label: str, class_name: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setProperty("class", class_name)
    return btn


def class_str(widget: QWidget) -> str:
    raw = widget.property("class")
    return "" if raw is None else str(raw)


class ReproBar(QWidget):
    def __init__(self, bar_screen: QScreen) -> None:
        super().__init__()
        self._target_screen = bar_screen
        self._padding = {"left": 8, "right": 8, "top": 6, "bottom": 6}
        self._dimensions = {"height": 60}
        self._bar_frame = QFrame(self)
        self._bar_frame.setProperty("class", "bar")
        self._bar_frame.installEventFilter(self)
        self.setWindowTitle("Repro: QGridLayout bar, class-toggle width")
        self.position_bar(init=True)

    def bar_pos(self) -> tuple[int, int]:
        screen_x = self._target_screen.geometry().x()
        screen_y = self._target_screen.geometry().y()
        x = int(screen_x + self._padding["left"])
        y = int(screen_y + self._padding["top"])
        return x, y

    def position_bar(self, init: bool = False) -> None:
        screen_width = self._target_screen.geometry().width()
        screen_height = self._target_screen.geometry().height()
        bar_width = screen_width - self._padding["left"] - self._padding["right"]
        bar_height = self._dimensions["height"]
        bar_x, bar_y = self.bar_pos()
        self.setGeometry(bar_x, bar_y, bar_width, bar_height)
        self._bar_frame.setGeometry(0, 0, bar_width, bar_height)
        print(
            f"[bar] position_bar init={init} screen={screen_width}x{screen_height} "
            f"bar=({bar_x},{bar_y},{bar_width},{bar_height})"
        )

    def on_geometry_changed(self, geo: QRect) -> None:
        print(f"[bar] screen geometry changed -> {geo}")
        self.position_bar()



def print_ws_state(engine: TransitionEngine, ws_buttons: list[QPushButton], header: str) -> None:
    print(f"\n=== {header} ===")
    for btn in ws_buttons:
        ctx = engine._contexts.get(id(btn))
        anim = ctx.active_animations.get("width") if ctx is not None else None
        state = "none"
        end_val = None
        current_time = None
        if anim is not None:
            state = anim.anim.state().name.lower()
            if hasattr(anim.anim, "endValue"):
                end_val = anim.anim.endValue()
            current_time = anim.anim.currentTime()
        css_props = {} if ctx is None else {k: v for k, v in ctx.css_anim_props.items() if "width" in k}
        print(
            f"btn={btn.text():>2} class={class_str(btn)!r} "
            f"width={btn.width():>4} hint={btn.sizeHint().width():>4} text={btn.text()!r} "
            f"min={btn.minimumWidth():>4} max={btn.maximumWidth():>4} "
            f"anim={state:>7} t={current_time!s:>4} end={end_val!s:>8} css={css_props}"
        )


def attach_width_debug(engine: TransitionEngine, btn: QPushButton) -> None:
    ctx = engine._contexts.get(id(btn))
    if ctx is None:
        return
    anim = ctx.active_animations.get("width")
    if anim is None or getattr(anim, "_debug_hooked", False):
        return
    setattr(anim, "_debug_hooked", True)

    def on_value_changed(value: object, button: QPushButton = btn) -> None:
        print(
            "---------------------------------------\n"
            f"[tick] btn={button.text()} class={class_str(button)!r} width={button.width()} "
            f"hint={button.sizeHint().width()} value={value} "
            f"min={button.minimumWidth()} max={button.maximumWidth()}"
        )

    def on_finished(button: QPushButton = btn) -> None:
        ctx_now = engine._contexts.get(id(button))
        css_props = {} if ctx_now is None else {k: v for k, v in ctx_now.css_anim_props.items() if 'width' in k}
        print(
            f"[done] btn={button.text()} class={class_str(button)!r} width={button.width()} "
            f"hint={button.sizeHint().width()} min={button.minimumWidth()} "
            f"max={button.maximumWidth()} css={css_props}"
        )

    anim.anim.valueChanged.connect(on_value_changed)
    anim.anim.finished.connect(on_finished)
    print(f"[hook] attached width debug to btn={btn.text()} end={anim.anim.endValue()}")


def main() -> None:
    app = QApplication(sys.argv)

    cleaned_qss, rules = extract_rules(REPRO_CSS)
    engine = TransitionEngine(rules, startup_delay_ms=200)
    app.installEventFilter(engine)
    app.setStyleSheet(cleaned_qss)

    assert (ps := app.primaryScreen()) is not None
    bar = ReproBar(ps)
    bar.setProperty("class", "bar")
    bar_frame = bar._bar_frame

    # QGridLayout with 3 columns matching parent app
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(0)
    switch_btn = None
    ws_buttons: list[QPushButton] = []
    active_index = 0

    for col, side in enumerate(("left", "center", "right")):
        container = QFrame()
        container.setProperty("class", f"container container-{side}")
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if side == "left":
            for txt in ("Vol", "Bat 87%"):
                b = QPushButton(txt)
                b.setProperty("class", "right-btn")
                layout.addWidget(b, 0)
            ws_container = QFrame()
            ws_container.setProperty("class", "container glazewm-workspaces")
            ws_layout = QHBoxLayout(ws_container)
            ws_layout.setContentsMargins(0, 0, 0, 0)
            ws_layout.setSpacing(0)
            for i in range(1, 8):
                cls = "ws-btn active_populated focused_populated" if i == 1 else "ws-btn populated"
                btn = make_ws_btn(str(i), cls)
                btn.setObjectName(f"ws-btn-{i}")
                ws_buttons.append(btn)
                ws_layout.addWidget(btn)
            layout.addWidget(ws_container, 0)
            for txt in ("CPU 12%", "MEM 4.2GB"):
                lbl = QLabel(txt)
                lbl.setProperty("class", "middle-label")
                layout.addWidget(lbl, 0)
            layout.addStretch(1)

        elif side == "center":
            layout.insertStretch(0, 1)
            for txt in ("CPU 12%", "MEM 4.2GB", "NET ↓120"):
                lbl = QLabel(txt)
                lbl.setProperty("class", "middle-label")
                layout.addWidget(lbl, 0)
            layout.addStretch(1)

        elif side == "right":
            layout.insertStretch(0, 1)
            for txt in ("Vol", "Bat 87%"):
                btn = QPushButton(txt)
                btn.setProperty("class", "right-btn")
                layout.addWidget(btn, 0)
            switch_btn = QPushButton("Next WS")
            switch_btn.setProperty("class", "right-btn")
            layout.addWidget(switch_btn, 0)

        container.setLayout(layout)
        grid.addWidget(container, 0, col)

    bar_frame.setLayout(grid)

    def dump_timed(header: str) -> None:
        print_ws_state(engine, ws_buttons, header)

    def rotate_active() -> None:
        nonlocal active_index
        old_idx = active_index
        new_idx = (active_index + 1) % len(ws_buttons)
        old_btn = ws_buttons[old_idx]
        new_btn = ws_buttons[new_idx]

        assert (layout := bar._bar_frame.layout()) is not None
        print_ws_state(engine, ws_buttons, "before switch")
        print(
            f"[bar] before switch outer={bar.geometry()} frame={bar._bar_frame.geometry()} "
            f"layout_hint={layout.sizeHint().width() if bar._bar_frame.layout() else 'n/a'}"
        )
        print(f"\n>>> switching focused workspace {old_btn.text()} -> {new_btn.text()}")
        old_btn.setProperty("class", "ws-btn active_populated")
        new_btn.setProperty("class", "ws-btn active_populated focused_populated")
        active_index = new_idx

        QTimer.singleShot(0, lambda: dump_timed("after process tick"))

    assert switch_btn is not None
    switch_btn.clicked.connect(rotate_active)

    bar.show()
    QTimer.singleShot(250, lambda: print_ws_state(engine, ws_buttons, "initial"))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
