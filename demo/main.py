import sys
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qt_css_engine import TransitionEngine, extract_rules

DISABLE_ANIMATIONS = False
STYLESHEET_PATH = "demo" / Path("styles.css")

dynamic_btns: list[QPushButton] = []


def add_widget(parent: QWidget) -> None:
    """Add a test .btn widget at the bottom of the layout to test dynamic properties"""
    btn = QPushButton("Dynamic (.btn) with applied styles and animation")
    btn.setProperty("class", "btn")
    if l := parent.layout():
        dynamic_btns.append(btn)
        l.addWidget(btn)


def remove_widget() -> None:
    """Remove the last .btn widget from the layout"""
    if not dynamic_btns:
        return
    dynamic_btns[-1].deleteLater()
    dynamic_btns.pop()


def hide_widget() -> None:
    """Hide the last .btn widget from the layout"""
    if not dynamic_btns:
        return
    if not dynamic_btns[-1].isHidden():
        dynamic_btns[-1].setHidden(True)
    else:
        dynamic_btns[-1].setHidden(False)


class TabLabel(QLabel):
    """QLabel that acts as a toggle tab. Clicking activates it and deactivates its siblings."""

    def __init__(self, text: str, group: list[TabLabel]) -> None:
        super().__init__(text)
        self._group = group
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, ev: QMouseEvent | None) -> None:
        if not ev:
            return
        for tab in self._group:
            tab.setProperty("class", "tab")
        self.setProperty("class", "tab active")
        super().mousePressEvent(ev)


def _dark_palette() -> QPalette:
    p = QPalette()
    dark = QColor("#1e1e1e")
    mid = QColor("#2d2d2d")
    light = QColor("#444444")
    text = QColor("#cccccc")
    bright = QColor("#ffffff")
    accent = QColor("#4d88ff")
    p.setColor(QPalette.ColorRole.Window, dark)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, dark)
    p.setColor(QPalette.ColorRole.AlternateBase,  mid)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.BrightText, bright)
    p.setColor(QPalette.ColorRole.Button,light)
    p.setColor(QPalette.ColorRole.ButtonText, bright)
    p.setColor(QPalette.ColorRole.Highlight,  accent)
    p.setColor(QPalette.ColorRole.HighlightedText, bright)
    p.setColor(QPalette.ColorRole.ToolTipBase, mid)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#666666"))
    return p


def main():
    app = QApplication(sys.argv)
    app.setPalette(_dark_palette())

    # Load stylesheet
    try:
        with open(STYLESHEET_PATH) as f:
            stylesheet = f.read()
    except FileNotFoundError:
        stylesheet = ""

    engine: TransitionEngine | None = None

    if not DISABLE_ANIMATIONS:
        # Parse and clean transitions from QSS
        cleaned_qss, rules = extract_rules(stylesheet)

        # Apply standard QSS to application
        app.setStyleSheet(cleaned_qss)

        # Setup Transition Engine
        engine = TransitionEngine(rules)
        app.installEventFilter(engine)

        # Hot-reload: watch styles.css for changes
        watcher = QFileSystemWatcher([str(STYLESHEET_PATH)])

        def _reload_css(path: str, _retries: int = 3) -> None:
            try:
                new_stylesheet = Path(path).read_text()
            except OSError:
                if _retries > 0:
                    QTimer.singleShot(100, lambda: _reload_css(path, _retries - 1))
                return
            new_cleaned_qss, new_rules = extract_rules(new_stylesheet)
            engine.reload_rules(new_rules)
            app.setStyleSheet(new_cleaned_qss)
            # Some editors replace the file rather than modify it, so re-add if lost
            if path not in watcher.files():
                watcher.addPath(path)

        watcher.fileChanged.connect(_reload_css)
    else:
        app.setStyleSheet(stylesheet)

    # UI setup
    window = QFrame()
    window.setWindowTitle("Qt CSS Animations Demo")
    window.resize(600, 1000)
    window_layout = QVBoxLayout(window)
    window_layout.setContentsMargins(0, 0, 0, 0)

    scroll_area = QScrollArea(window)
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.Shape.NoFrame)
    window_layout.addWidget(scroll_area)

    inner_widget = QFrame()
    scroll_area.setWidget(inner_widget)
    layout = QVBoxLayout(inner_widget)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    layout.setContentsMargins(50, 50, 50, 50)
    layout.setSpacing(20)

    # Three buttons for testing size hint based transitions
    container = QFrame()
    container.setProperty("class", "size-hint-container")
    container.setContentsMargins(0, 0, 0, 0)
    container_layout = QHBoxLayout(container)
    container_layout.setContentsMargins(0, 0, 0, 0)
    container_layout.setSpacing(0)
    container_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    btn1 = QPushButton("btn 1")
    btn1.setProperty("class", "btn-custom")
    btn1.setContentsMargins(0, 0, 0, 0)
    container_layout.addWidget(btn1)

    btn2 = QPushButton("btn 2 a bit longer")
    btn2.setProperty("class", "btn-custom")
    btn2.setContentsMargins(0, 0, 0, 0)
    container_layout.addWidget(btn2)

    btn3 = QPushButton("btn 3")
    btn3.setProperty("class", "btn-custom")
    btn3.setContentsMargins(0, 0, 0, 0)
    container_layout.addWidget(btn3)

    layout.addWidget(container)

    # Multiple buttons in one container and multiple containers
    multi_container = QFrame()
    multi_container.setProperty("class", "workspaces")
    multi_container.setContentsMargins(0, 0, 0, 0)
    multi_container_layout = QHBoxLayout(multi_container)
    multi_container_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
    multi_container_layout.setContentsMargins(0, 0, 0, 0)
    multi_container_layout.setSpacing(0)

    for i in range(3):
        _m_container = QFrame()
        _m_container.setProperty("class", "ws-btn")
        _m_container.setObjectName(f"ws-btn-{i}")
        _m_container_layout = QHBoxLayout(_m_container)
        _m_container_layout.setContentsMargins(0, 0, 0, 0)
        _m_container_layout.setSpacing(0)
        for j in range(2):
            btn = QPushButton(f"{i}-{j}")
            btn.setProperty("class", "m-btn")
            _m_container_layout.addWidget(btn)

        multi_container_layout.addWidget(_m_container)

    layout.addWidget(multi_container)

    # A box matching #box
    box = QLabel("Box (#box)\nHover for box shadow")
    box.setObjectName("box")
    box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(box)

    # A button matching .btn
    btn = QPushButton("Button (.btn)")
    btn.setProperty("class", "btn")
    layout.addWidget(btn)

    btn_container = QFrame()
    btn_container.setProperty("class", "btn-container")
    btn_container_layout = QHBoxLayout(btn_container)
    btn_container_layout.setContentsMargins(0, 0, 0, 0)
    btn_container_layout.setSpacing(5)
    layout.addWidget(btn_container)

    btn = QPushButton("Add (.btn)")
    btn.setProperty("class", "btn")
    btn.clicked.connect(lambda: add_widget(inner_widget))
    btn_container_layout.addWidget(btn)

    btn = QPushButton("Remove (.btn)")
    btn.setProperty("class", "btn")
    btn.clicked.connect(lambda: remove_widget())
    btn_container_layout.addWidget(btn)

    btn = QPushButton("Hide (.btn)")
    btn.setProperty("class", "btn")
    btn.clicked.connect(lambda: hide_widget())
    btn_container_layout.addWidget(btn)

    # A reveal button matching #reveal
    reveal = QPushButton("Reveal Me (#reveal/opacity)")
    reveal.setObjectName("reveal")
    layout.addWidget(reveal)

    # A label matching .label
    label = QLabel("QLabel (.label) supports :pressed")
    label.setProperty("class", "label")
    layout.addWidget(label)

    # A container to test nested selectors (.sidebar .action)
    sidebar = QFrame()
    sidebar.setProperty("class", "sidebar")
    sidebar_layout = QVBoxLayout(sidebar)
    sidebar_layout.setContentsMargins(0, 0, 0, 0)

    action_btn = QPushButton("Nested (.sidebar .action)")
    action_btn.setProperty("class", "action")
    sidebar_layout.addWidget(action_btn)

    layout.addWidget(sidebar)

    # A text edit to test :focus transitions
    editor_container = QFrame()
    hor_layout = QHBoxLayout(editor_container)
    hor_layout.setContentsMargins(0, 0, 0, 0)
    hor_layout.setSpacing(0)
    for _ in range(2):
        editor = QTextEdit("Click to focus…")
        editor.setProperty("class", "editor")
        editor.setFixedHeight(80)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hor_layout.addWidget(editor)
    layout.addWidget(editor_container)

    # Two tab labels — click to toggle active state (.tab / .tab.active)
    tab_group: list[TabLabel] = []
    tab_row = QFrame()
    tab_row_layout = QHBoxLayout(tab_row)
    tab_row_layout.setContentsMargins(0, 0, 0, 0)
    tab_row_layout.setSpacing(0)
    for title in ("Tab One", "Tab Two"):
        tab = TabLabel(title, tab_group)
        tab.setProperty("class", "tab")
        tab_group.append(tab)
        tab_row_layout.addWidget(tab)
    tab_group[0].setProperty("class", "tab active")  # first tab starts active
    layout.addWidget(tab_row)

    # DynamicPropertyChange test: clicking the trigger cycles .active across three buttons.
    # The active button gains min/max-width + color — exercises class-change size animation.
    cycle_outer = QFrame()
    cycle_outer_layout = QVBoxLayout(cycle_outer)
    cycle_outer_layout.setContentsMargins(0, 0, 0, 0)
    cycle_outer_layout.setSpacing(6)

    cycle_trigger = QPushButton("Cycle .active class →")
    cycle_trigger.setProperty("class", "btn")
    cycle_outer_layout.addWidget(cycle_trigger)

    cycle_row = QFrame()
    cycle_row_layout = QHBoxLayout(cycle_row)
    cycle_row_layout.setContentsMargins(0, 0, 0, 0)
    cycle_row_layout.setSpacing(8)
    cycle_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

    cycle_btns: list[QPushButton] = []
    for i in range(4):
        rb = QPushButton(f"Item {i + 1}")
        rb.setProperty("class", "cycle-btn active" if i == 0 else "cycle-btn")
        cycle_row_layout.addWidget(rb)
        cycle_btns.append(rb)

    cycle_outer_layout.addWidget(cycle_row)
    layout.addWidget(cycle_outer)

    _active_idx = [0]

    def _cycle_active() -> None:
        _active_idx[0] = (_active_idx[0] + 1) % len(cycle_btns)
        for i, rb in enumerate(cycle_btns):
            rb.setProperty("class", "cycle-btn active" if i == _active_idx[0] else "cycle-btn")

    cycle_trigger.clicked.connect(_cycle_active)

    # Dynamic label that cycles through class states on a timer
    dynamic_label = QLabel("Dynamic Label (QTimer)")
    dynamic_label.setProperty("class", "dynamic-label")
    dynamic_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(dynamic_label)

    _class_cycle = ["dynamic-label", "dynamic-label state1", "dynamic-label state2"]
    _cycle_idx = [0]

    def _cycle_label_class() -> None:
        _cycle_idx[0] = (_cycle_idx[0] + 1) % len(_class_cycle)
        dynamic_label.setText(_class_cycle[_cycle_idx[0]])
        dynamic_label.setProperty("class", _class_cycle[_cycle_idx[0]])

    _label_timer = QTimer()
    _label_timer.setInterval(2000)
    _label_timer.timeout.connect(_cycle_label_class)
    _label_timer.start()

    checkable_btn = QPushButton("Checkable Btn")
    checkable_btn.setProperty("class", "checkable-btn")
    checkable_btn.setCheckable(True)
    checkable_btn.setChecked(False)
    def _check(status: bool):
        checkable_btn.setText("Checked" if status else "Unchecked")
    checkable_btn.clicked.connect(_check)
    layout.addWidget(checkable_btn)

    # :clicked demo — animation always plays full forward then full reverse
    clicked_btn = QPushButton(":clicked — full round-trip animation")
    clicked_btn.setProperty("class", "clicked-btn")
    layout.addWidget(clicked_btn)

    # :active demo — widget transitions when window gains/loses focus
    active_row = QFrame()
    active_row_layout = QHBoxLayout(active_row)
    active_row_layout.setContentsMargins(0, 0, 0, 0)
    active_row_layout.setSpacing(8)

    active_lbl = QLabel(":active — lights up when window is focused")
    active_lbl.setProperty("class", "active-demo")
    active_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    active_row_layout.addWidget(active_lbl, stretch=1)

    popup_btn = QPushButton("Open Popup")
    popup_btn.setProperty("class", "btn")
    active_row_layout.addWidget(popup_btn)

    layout.addWidget(active_row)

    _popups: list[QWidget] = []

    def _open_popup() -> None:
        popup = QWidget(window, Qt.WindowType.Dialog)
        popup.setWindowTitle("Popup — click main window to restore :active")
        popup.resize(320, 80)
        popup_inner = QVBoxLayout(popup)
        popup_inner.addWidget(QLabel("Click the main window to trigger :active transition."))
        _popups.append(popup)
        popup.destroyed.connect(lambda: _popups.remove(popup) if popup in _popups else None)
        popup.show()

    popup_btn.clicked.connect(_open_popup)

    # steps() easing demo — hover each button to see discrete color jumps
    steps_row = QFrame()
    steps_row.setProperty("class", "steps-row")
    steps_row_layout = QHBoxLayout(steps_row)
    steps_row_layout.setContentsMargins(0, 0, 0, 0)
    steps_row_layout.setSpacing(6)
    steps_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
    for variant, label in (
        ("s-jump-end",   "steps(5, jump-end)"),
        ("s-jump-start", "steps(5, jump-start)"),
        ("s-jump-none",  "steps(5, jump-none)"),
        ("s-step-start", "step-start"),
    ):
        b = QPushButton(label)
        b.setProperty("class", f"steps-btn {variant}")
        steps_row_layout.addWidget(b)
    layout.addWidget(steps_row)

    # Fallthrough test — right and middle buttons trigger parent widget
    fallthrough_base = QFrame()
    fallthrough_base.setProperty("class", "fallthrough-row")
    fallthrough_layout = QHBoxLayout(fallthrough_base)
    fallthrough_layout.setContentsMargins(0, 0, 0, 0)
    fallthrough_layout.setSpacing(6)
    fallthrough_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
    for variant, label in (
        ("btn1", "btn1"),
        ("btn2", "btn2"),
        ("btn3", "btn3"),
        ("btn4", "btn4"),
    ):
        b = QPushButton(label)
        b.setProperty("class", f"fallthrough-btn {variant}")
        fallthrough_layout.addWidget(b)
    layout.addWidget(fallthrough_base)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
