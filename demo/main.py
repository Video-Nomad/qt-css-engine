import logging
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qt_css_engine import TransitionEngine, extract_rules

STYLESHEET_PATH = Path(__file__).resolve().parent / "styles.css"

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

dynamic_btns: list[QPushButton] = []


class TabLabel(QLabel):
    """QLabel that acts as a toggle tab. Clicking activates it and deactivates its siblings."""

    def __init__(self, text: str, group: list[TabLabel]) -> None:
        super().__init__(text)
        self._group = group
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev: QMouseEvent | None) -> None:
        if ev is None:
            return
        for tab in self._group:
            tab.setProperty("class", "tab")
        self.setProperty("class", "tab active")
        super().mousePressEvent(ev)


def _dark_palette() -> QPalette:
    p = QPalette()
    dark = QColor("#121216")
    mid = QColor("#1b1b22")
    light = QColor("#2e2e38")
    text = QColor("#e8e8ec")
    muted = QColor("#9a9aa5")
    bright = QColor("#ffffff")
    accent = QColor("#4d88ff")
    p.setColor(QPalette.ColorRole.Window, dark)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, dark)
    p.setColor(QPalette.ColorRole.AlternateBase, mid)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.BrightText, bright)
    p.setColor(QPalette.ColorRole.Button, light)
    p.setColor(QPalette.ColorRole.ButtonText, bright)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, bright)
    p.setColor(QPalette.ColorRole.ToolTipBase, mid)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    return p


def add_widget(host: QWidget) -> None:
    """Append a dynamic .btn to the playground host layout."""
    btn = QPushButton("Dynamic (.btn) — added at runtime")
    btn.setProperty("class", "btn")
    if (layout := host.layout()) is not None:
        dynamic_btns.append(btn)
        layout.addWidget(btn)


def remove_widget() -> None:
    """Remove the last dynamic .btn."""
    if not dynamic_btns:
        return
    dynamic_btns[-1].deleteLater()
    dynamic_btns.pop()


def hide_widget() -> None:
    """Toggle visibility of the last dynamic .btn."""
    if not dynamic_btns:
        return
    last = dynamic_btns[-1]
    last.setHidden(not last.isHidden())


def make_card(title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout]:
    """Build a .card section frame; returns (card, body_layout_for_widgets)."""
    card = QFrame()
    card.setProperty("class", "card")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(20, 18, 20, 18)
    card_layout.setSpacing(12)
    title_lbl = QLabel(title)
    title_lbl.setProperty("class", "card-title")
    sub_lbl = QLabel(subtitle)
    sub_lbl.setProperty("class", "card-sub")
    sub_lbl.setWordWrap(True)
    card_layout.addWidget(title_lbl)
    card_layout.addWidget(sub_lbl)
    body = QVBoxLayout()
    body.setSpacing(10)
    card_layout.addLayout(body)
    return card, body


def make_row(spacing: int = 8) -> tuple[QFrame, QHBoxLayout]:
    """Bare horizontal row container (no frame styling)."""
    frame = QFrame()
    row_layout = QHBoxLayout(frame)
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.setSpacing(spacing)
    row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return frame, row_layout


def make_hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("class", "hint")
    lbl.setWordWrap(True)
    return lbl


def make_editor_row() -> tuple[QFrame, list[QTextEdit]]:
    """Two side-by-side .editor text edits for :focus testing."""
    frame = QFrame()
    hor_layout = QHBoxLayout(frame)
    hor_layout.setContentsMargins(0, 0, 0, 0)
    hor_layout.setSpacing(10)
    editors: list[QTextEdit] = []
    for i in range(2):
        editor = QTextEdit(f"Editor {i + 1} — click or Tab to focus…")
        editor.setProperty("class", "editor")
        editor.setFixedHeight(72)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hor_layout.addWidget(editor)
        editors.append(editor)
    return frame, editors


def main() -> None:
    app = QApplication(sys.argv)
    app.setPalette(_dark_palette())

    try:
        stylesheet = STYLESHEET_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("Stylesheet not found: %s", STYLESHEET_PATH)
        stylesheet = ""

    cleaned_qss, rules = extract_rules(stylesheet)
    app.setStyleSheet(cleaned_qss)
    engine = TransitionEngine(rules)
    app.installEventFilter(engine)

    # Hot-reload: watch styles.css for changes.
    watcher = QFileSystemWatcher([str(STYLESHEET_PATH)])

    def _reload_css(path: str, _retries: int = 3) -> None:
        try:
            new_stylesheet = Path(path).read_text(encoding="utf-8")
        except OSError:
            if _retries > 0:
                QTimer.singleShot(100, lambda: _reload_css(path, _retries - 1))
            return
        new_cleaned_qss, new_rules = extract_rules(new_stylesheet)
        engine.reload_rules(new_rules)
        app.setStyleSheet(new_cleaned_qss)
        # Some editors replace the file rather than modify it, so re-add if lost.
        if path not in watcher.files():
            watcher.addPath(path)

    watcher.fileChanged.connect(_reload_css)

    # ------------------------------------------------------------------ shell
    window = QWidget()
    window.setObjectName("app-window")
    window.setWindowTitle("Qt CSS Engine — Gallery")
    window.resize(1160, 800)
    window_layout = QVBoxLayout(window)
    window_layout.setContentsMargins(0, 0, 0, 0)
    window_layout.setSpacing(0)

    header = QFrame()
    header.setProperty("class", "app-header")
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(24, 16, 24, 16)
    header_layout.setSpacing(12)
    title_box = QVBoxLayout()
    title_box.setSpacing(2)
    app_title = QLabel("Qt CSS Engine")
    app_title.setProperty("class", "app-title")
    app_sub = QLabel("Transition gallery — hover, focus, press, check, click, class changes")
    app_sub.setProperty("class", "app-sub")
    title_box.addWidget(app_title)
    title_box.addWidget(app_sub)
    header_layout.addLayout(title_box)
    header_layout.addStretch(1)
    reload_path = QLabel(str(STYLESHEET_PATH.name))
    reload_path.setProperty("class", "reload-path")
    reload_pill = QLabel("● hot-reload on")
    reload_pill.setProperty("class", "reload-pill")
    header_layout.addWidget(reload_path)
    header_layout.addWidget(reload_pill)
    window_layout.addWidget(header)

    body = QFrame()
    body.setProperty("class", "app-body")
    body_layout = QHBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(0)
    window_layout.addWidget(body, stretch=1)

    # ------------------------------------------------------------- left nav
    nav = QFrame()
    nav.setProperty("class", "app-nav")
    nav.setFixedWidth(216)
    nav_layout = QVBoxLayout(nav)
    nav_layout.setContentsMargins(14, 18, 14, 18)
    nav_layout.setSpacing(4)
    nav_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    nav_heading = QLabel("GALLERY")
    nav_heading.setProperty("class", "nav-heading")
    nav_layout.addWidget(nav_heading)

    nav_items: list[tuple[str, str]] = [
        ("buttons", "Buttons and press"),
        ("inputs", "Focus and checked"),
        ("effects", "Opacity and shadow"),
        ("easing", "Easing"),
        ("timing", "Delay and duration"),
        ("size", "Size and shape"),
        ("dynamic", "Dynamic classes"),
        ("nesting", "Nesting and scope"),
        ("attrs", "Attributes & specificity"),
        ("playground", "Playground"),
    ]

    scroll_area = QScrollArea()
    scroll_area.setProperty("class", "app-scroll")
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.Shape.NoFrame)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    content = QFrame()
    content.setProperty("class", "app-content")
    scroll_area.setWidget(content)
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(24, 24, 24, 32)
    content_layout.setSpacing(16)
    content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    cards: dict[str, QFrame] = {}

    def _scroll_to(key: str) -> None:
        if (card := cards.get(key)) is not None:
            scroll_area.ensureWidgetVisible(card, 0, 60)

    def _nav_handler(target: str) -> Callable[[bool], None]:
        def _go(_checked: bool = False) -> None:
            _scroll_to(target)

        return _go

    for key, label_text in nav_items:
        nav_btn = QPushButton(label_text)
        nav_btn.setProperty("class", "nav-btn")
        nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        nav_btn.clicked.connect(_nav_handler(key))
        nav_layout.addWidget(nav_btn)
    nav_layout.addStretch(1)
    nav_hint = QLabel("Tip: edit demo/styles.css — changes apply instantly.")
    nav_hint.setProperty("class", "nav-hint")
    nav_hint.setWordWrap(True)
    nav_layout.addWidget(nav_hint)

    body_layout.addWidget(nav)
    body_layout.addWidget(scroll_area, stretch=1)

    # ------------------------------------------------------------ intro card
    intro_card, intro_body = make_card(
        "Welcome",
        "The engine strips `transition:` declarations from QSS, tracks pseudo-states "
        "(:hover :focus :pressed :checked :active + the engine-only :clicked), and animates "
        "matching properties out-of-band. Everything below is live — hover it, hold it, "
        "Tab through it, click it.",
    )
    stats_frame, stats_row = make_row()
    for stat in ("10 sections", "6 pseudo-states", "20+ animatable props", ":clicked round-trip"):
        stat_lbl = QLabel(stat)
        stat_lbl.setProperty("class", "stat")
        stats_row.addWidget(stat_lbl)
    intro_body.addWidget(stats_frame)
    content_layout.addWidget(intro_card)

    # ------------------------------------------------- 1. buttons & press
    btn_card, btn_body = make_card(
        "Buttons & press states",
        "Hover morphs shape and color. Press-and-hold shows :pressed. "
        ":clicked is engine-only — it always plays the full loop on click.",
    )
    row1_frame, row1 = make_row()
    primary_btn = QPushButton("Primary (.btn)")
    primary_btn.setProperty("class", "btn")
    primary_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    row1.addWidget(primary_btn)
    hover_btn = QPushButton("Hover me — pill morph (.btn)")
    hover_btn.setProperty("class", "btn")
    hover_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    row1.addWidget(hover_btn)
    pressed_label = QLabel("QLabel (.label) — press & hold")
    pressed_label.setProperty("class", "label")
    pressed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row1.addWidget(pressed_label, stretch=1)
    btn_body.addWidget(row1_frame)

    per_prop_frame = QFrame()
    per_prop_frame.setProperty("class", "btn-container")
    per_prop_layout = QHBoxLayout(per_prop_frame)
    per_prop_layout.setContentsMargins(0, 0, 0, 0)
    per_prop_layout.setSpacing(8)
    for text in ("Fast text", "Slow border", "Mid bg"):
        b = QPushButton(text)
        b.setProperty("class", "btn")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        per_prop_layout.addWidget(b)
    btn_body.addWidget(per_prop_frame)
    btn_body.addWidget(make_hint("Above row: per-property timing — text 200ms, background 300ms, border 600ms."))

    clicked_btn = QPushButton("Click me — :clicked plays forward + reverse")
    clicked_btn.setProperty("class", "clicked-btn")
    clicked_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_body.addWidget(clicked_btn)
    content_layout.addWidget(btn_card)
    cards["buttons"] = btn_card

    # ------------------------------------------------- 2. focus & checked
    inputs_card, inputs_body = make_card(
        "Focus & checked",
        "Click or press Tab to move :focus between fields. Toggle the buttons for :checked.",
    )
    editor_frame, _editors = make_editor_row()
    inputs_body.addWidget(editor_frame)
    search = QLineEdit()
    search.setPlaceholderText("QLineEdit (.editor) — focus glow works here too…")
    search.setProperty("class", "editor")
    search.setFixedHeight(36)
    inputs_body.addWidget(search)

    check_frame, check_row = make_row(spacing=10)
    checkable_btn = QPushButton("Unchecked")
    checkable_btn.setProperty("class", "checkable-btn")
    checkable_btn.setCheckable(True)
    checkable_btn.setChecked(False)
    checkable_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _on_checkable_toggled(status: bool) -> None:
        checkable_btn.setText("Checked" if status else "Unchecked")

    checkable_btn.toggled.connect(_on_checkable_toggled)
    check_row.addWidget(checkable_btn)

    pre_checked = QPushButton("Pre-checked")
    pre_checked.setProperty("class", "checkable-btn")
    pre_checked.setCheckable(True)
    pre_checked.setChecked(True)
    pre_checked.setCursor(Qt.CursorShape.PointingHandCursor)

    def _on_pre_checked_toggled(on: bool) -> None:
        pre_checked.setText("Checked" if on else "Unchecked")

    pre_checked.toggled.connect(_on_pre_checked_toggled)
    check_row.addWidget(pre_checked)

    agree = QCheckBox("QCheckBox (.check-box) — toggles :checked")
    agree.setProperty("class", "check-box")
    agree.setCursor(Qt.CursorShape.PointingHandCursor)
    check_row.addWidget(agree)
    inputs_body.addWidget(check_frame)
    inputs_body.addWidget(
        make_hint("Checked + pressed combine: hold the mouse down on a checked button for :checked:pressed.")
    )
    content_layout.addWidget(inputs_card)
    cards["inputs"] = inputs_card

    # ------------------------------------------------- 3. opacity & shadow
    fx_card, fx_body = make_card(
        "Opacity, shadow & cursor",
        "Opacity and shadow each need the widget's single graphics-effect slot — "
        "keep them on separate widgets. Cursor and gradients are static (no transition).",
    )
    fx_row_frame, fx_row = make_row(spacing=12)
    box = QLabel("Shadow card (#box)\nHover for glow")
    box.setObjectName("box")
    box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    box.setFixedHeight(110)
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    fx_row.addWidget(box)
    reveal = QPushButton("Fade in (#reveal)\nHover for opacity")
    reveal.setObjectName("reveal")
    reveal.setFixedHeight(110)
    reveal.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    reveal.setCursor(Qt.CursorShape.PointingHandCursor)
    fx_row.addWidget(reveal)
    fx_body.addWidget(fx_row_frame)

    grad_frame, grad_row = make_row(spacing=10)
    for cls, text in (("grad grad-a", "linear"), ("grad grad-b", "radial"), ("grad grad-c", "conic")):
        g = QLabel(text)
        g.setProperty("class", cls)
        g.setAlignment(Qt.AlignmentFlag.AlignCenter)
        g.setFixedHeight(56)
        g.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grad_row.addWidget(g)
    fx_body.addWidget(grad_frame)
    fx_body.addWidget(make_hint("Static gradients — rendered once, never animated."))

    cursor_frame, cursor_row = make_row(spacing=8)
    for variant, text in (
        ("cursor-demo cursor-pointer", "pointer"),
        ("cursor-demo cursor-text", "text"),
        ("cursor-demo cursor-crosshair", "crosshair"),
        ("cursor-demo cursor-grab", "grab"),
        ("cursor-demo cursor-wait", "wait"),
        ("cursor-demo cursor-not-allowed", "nope"),
    ):
        chip = QLabel(text)
        chip.setProperty("class", variant)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cursor_row.addWidget(chip)
    fx_body.addWidget(cursor_frame)
    content_layout.addWidget(fx_card)
    cards["effects"] = fx_card

    # ------------------------------------------------- 4. easing lab
    easing_card, easing_body = make_card(
        "Easing",
        "Same hover target, different timing functions. Steps variants take 1500ms so the discrete jumps are visible.",
    )
    ease_grid_frame = QFrame()
    ease_grid = QGridLayout(ease_grid_frame)
    ease_grid.setContentsMargins(0, 0, 0, 0)
    ease_grid.setSpacing(8)
    ease_buttons: list[tuple[str, str]] = [
        ("ease-btn e-linear", "linear"),
        ("ease-btn e-ease", "ease"),
        ("ease-btn e-ease-in", "ease-in"),
        ("ease-btn e-ease-out", "ease-out"),
        ("ease-btn e-ease-in-out", "ease-in-out"),
        ("ease-btn e-overshoot", "overshoot"),
    ]
    for i, (cls, text) in enumerate(ease_buttons):
        b = QPushButton(text)
        b.setProperty("class", cls)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        ease_grid.addWidget(b, i // 3, i % 3)
    easing_body.addWidget(ease_grid_frame)

    steps_frame, steps_row = make_row(spacing=8)
    for variant, text in (
        ("s-jump-end", "steps(5, jump-end)"),
        ("s-jump-start", "steps(5, jump-start)"),
        ("s-jump-none", "steps(5, jump-none)"),
        ("s-step-start", "step-start"),
    ):
        b = QPushButton(text)
        b.setProperty("class", f"steps-btn {variant}")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        steps_row.addWidget(b)
    easing_body.addWidget(steps_frame)
    content_layout.addWidget(easing_card)
    cards["easing"] = easing_card

    # ------------------------------------------------- 5. delay & duration
    timing_card, timing_body = make_card(
        "Delay & duration",
        "Positive delay freezes the start value first. Negative delay jumps part-way into the timeline.",
    )
    delay_frame, delay_row = make_row(spacing=8)
    for cls, text in (
        ("delay-btn d-none", "no delay"),
        ("delay-btn d-wait", "+400ms delay"),
        ("delay-btn d-negative", "−250ms head-start"),
    ):
        b = QPushButton(text)
        b.setProperty("class", cls)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        delay_row.addWidget(b)
    timing_body.addWidget(delay_frame)
    longhand_btn = QPushButton("Longhands — bg 300ms, corners 800ms")
    longhand_btn.setProperty("class", "longhand-btn")
    longhand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    timing_body.addWidget(longhand_btn)
    timing_body.addWidget(
        make_hint("Longhand demo uses transition-property / duration / timing-function lists (see styles.css).")
    )
    content_layout.addWidget(timing_card)
    cards["timing"] = timing_card

    # ------------------------------------------------- 6. size & shape
    size_card, size_body = make_card(
        "Size & shape morph",
        "Width constraints, padding, border and type metrics all interpolate and re-lay-out smoothly.",
    )
    size_hint_box = QFrame()
    size_hint_box.setProperty("class", "size-hint-container")
    size_hint_layout = QHBoxLayout(size_hint_box)
    size_hint_layout.setContentsMargins(0, 0, 0, 0)
    size_hint_layout.setSpacing(10)
    size_hint_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    for text in ("btn 1", "btn 2 a bit longer", "btn 3"):
        sb = QPushButton(text)
        sb.setProperty("class", "btn-custom")
        sb.setCursor(Qt.CursorShape.PointingHandCursor)
        size_hint_layout.addWidget(sb)
    size_body.addWidget(size_hint_box)
    size_body.addWidget(make_hint("Hover: every button stretches to a shared 150px width."))

    shape_frame, shape_row = make_row(spacing=8)
    for cls, text in (
        ("morph-btn m-radius", "radius → pill"),
        ("morph-btn m-border", "border grows"),
        ("morph-btn m-pad", "padding grows"),
    ):
        b = QPushButton(text)
        b.setProperty("class", cls)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        shape_row.addWidget(b)
    size_body.addWidget(shape_frame)

    type_frame, type_row = make_row(spacing=8)
    size_btn = QPushButton("font-size 13 → 20px")
    size_btn.setProperty("class", "type-btn t-size")
    size_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    type_row.addWidget(size_btn)
    weight_btn = QPushButton("weight 400 → 900")
    weight_btn.setProperty("class", "type-btn t-weight")
    weight_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    type_row.addWidget(weight_btn)
    spacing_lbl = QLabel("letter-spacing widens")
    spacing_lbl.setProperty("class", "type-label-spacing")
    spacing_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    type_row.addWidget(spacing_lbl, stretch=1)
    size_body.addWidget(type_frame)
    content_layout.addWidget(size_card)
    cards["size"] = size_card

    # ------------------------------------------------- 7. dynamic classes
    dyn_card, dyn_body = make_card(
        "Dynamic classes",
        "Changing `class` re-runs the cascade and animates. Click the tabs, cycle the row, or watch the timer.",
    )
    tab_group: list[TabLabel] = []
    tab_row_frame = QFrame()
    tab_row_layout = QHBoxLayout(tab_row_frame)
    tab_row_layout.setContentsMargins(0, 0, 0, 0)
    tab_row_layout.setSpacing(0)
    for title in ("Tab One", "Tab Two", "Tab Three"):
        tab = TabLabel(title, tab_group)
        tab.setProperty("class", "tab")
        tab_group.append(tab)
        tab_row_layout.addWidget(tab)
    tab_group[0].setProperty("class", "tab active")
    dyn_body.addWidget(tab_row_frame)

    cycle_trigger = QPushButton("Cycle .active →")
    cycle_trigger.setProperty("class", "btn")
    cycle_trigger.setCursor(Qt.CursorShape.PointingHandCursor)
    dyn_body.addWidget(cycle_trigger)
    cycle_frame, cycle_row = make_row(spacing=8)
    cycle_btns: list[QPushButton] = []
    for i in range(4):
        rb = QPushButton(f"Item {i + 1}")
        rb.setProperty("class", "cycle-btn active" if i == 0 else "cycle-btn")
        rb.setCursor(Qt.CursorShape.PointingHandCursor)
        cycle_row.addWidget(rb)
        cycle_btns.append(rb)
    dyn_body.addWidget(cycle_frame)
    active_idx = [0]

    def _cycle_active() -> None:
        active_idx[0] = (active_idx[0] + 1) % len(cycle_btns)
        for i, rb in enumerate(cycle_btns):
            rb.setProperty("class", "cycle-btn active" if i == active_idx[0] else "cycle-btn")

    cycle_trigger.clicked.connect(_cycle_active)

    dynamic_label = QLabel("Dynamic Label (QTimer)")
    dynamic_label.setProperty("class", "dynamic-label")
    dynamic_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dyn_body.addWidget(dynamic_label)
    class_cycle = ["dynamic-label", "dynamic-label state1", "dynamic-label state2"]
    cycle_pos = [0]

    def _cycle_label_class() -> None:
        cycle_pos[0] = (cycle_pos[0] + 1) % len(class_cycle)
        dynamic_label.setText(class_cycle[cycle_pos[0]])
        dynamic_label.setProperty("class", class_cycle[cycle_pos[0]])

    label_timer = QTimer(window)
    label_timer.setInterval(2000)
    label_timer.timeout.connect(_cycle_label_class)
    label_timer.start()
    content_layout.addWidget(dyn_card)
    cards["dynamic"] = dyn_card

    # ------------------------------------------------- 8. nesting & scope
    nest_card, nest_body = make_card(
        "Nesting & scope",
        "Descendant selectors match through the tree. Hover the containers — children react to ancestor state.",
    )
    sidebar = QFrame()
    sidebar.setProperty("class", "sidebar")
    sidebar_layout = QVBoxLayout(sidebar)
    sidebar_layout.setContentsMargins(14, 14, 14, 14)
    sidebar_layout.setSpacing(10)
    sidebar_title = QLabel(".sidebar — hover morphs the frame radius")
    sidebar_title.setProperty("class", "sidebar-title")
    sidebar_layout.addWidget(sidebar_title)
    action_btn = QPushButton("Nested (.sidebar .action) — hover glows")
    action_btn.setProperty("class", "action")
    action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    sidebar_layout.addWidget(action_btn)

    parent_row = QFrame()
    parent_row_layout = QHBoxLayout(parent_row)
    parent_row_layout.setContentsMargins(0, 0, 0, 0)
    parent_row_layout.setSpacing(8)
    after_insert_btn = QPushButton("insertWidget → setProperty")
    parent_row_layout.insertWidget(0, after_insert_btn)
    after_insert_btn.setProperty("class", "action")
    before_insert_btn = QPushButton("setProperty → insertWidget")
    before_insert_btn.setProperty("class", "action")
    parent_row_layout.insertWidget(1, before_insert_btn)
    sidebar_layout.addWidget(parent_row)
    nest_body.addWidget(sidebar)
    nest_body.addWidget(make_hint("Reparenting edge cases: both insert orders must pick up .sidebar .action."))

    ws_box = QFrame()
    ws_box.setProperty("class", "workspaces")
    ws_layout = QHBoxLayout(ws_box)
    ws_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
    ws_layout.setContentsMargins(0, 0, 0, 0)
    ws_layout.setSpacing(10)
    for i in range(3):
        ws = QFrame()
        ws.setProperty("class", "ws-btn")
        ws.setObjectName(f"ws-btn-{i}")
        ws_inner = QHBoxLayout(ws)
        ws_inner.setContentsMargins(6, 6, 6, 6)
        ws_inner.setSpacing(6)
        for j in range(2):
            mb = QPushButton(f"{i}-{j}")
            mb.setProperty("class", "m-btn")
            ws_inner.addWidget(mb)
        ws_layout.addWidget(ws)
    nest_body.addWidget(ws_box)
    nest_body.addWidget(make_hint("Hover a .ws-btn pill — padding, radius and width ease out over 1s."))

    cpu_popup = QFrame()
    cpu_popup.setProperty("class", "cpu-popup")
    cpu_layout = QVBoxLayout(cpu_popup)
    cpu_layout.setContentsMargins(0, 0, 0, 0)
    cpu_header = QFrame()
    cpu_header.setProperty("class", "header")
    cpu_header_layout = QHBoxLayout(cpu_header)
    cpu_header_layout.setContentsMargins(0, 0, 0, 0)
    pin_btn = QPushButton("📌 Pin (.cpu-popup .header .pin-btn)")
    pin_btn.setProperty("class", "pin-btn")
    pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cpu_header_layout.addWidget(pin_btn)
    cpu_layout.addWidget(cpu_header)
    nest_body.addWidget(cpu_popup)

    fall_frame = QFrame()
    fall_frame.setProperty("class", "fallthrough-row")
    fall_layout = QHBoxLayout(fall_frame)
    fall_layout.setContentsMargins(10, 10, 10, 10)
    fall_layout.setSpacing(8)
    fall_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
    for variant in ("btn1", "btn2", "btn3", "btn4"):
        fb = QPushButton(variant)
        fb.setProperty("class", f"fallthrough-btn {variant}")
        fb.setCursor(Qt.CursorShape.PointingHandCursor)
        fall_layout.addWidget(fb)
    nest_body.addWidget(fall_frame)
    nest_body.addWidget(make_hint("Click a child: its own :pressed/:clicked fires and the parent should not change."))
    content_layout.addWidget(nest_card)
    cards["nesting"] = nest_card

    # ------------------------------------------------- 9. attributes & specificity
    attr_card, attr_body = make_card(
        "Attributes & specificity",
        "Dynamic properties drive styles via [attr=value] (bare or quoted). "
        "IDs beat classes regardless of source order; Type#id needs both to match.",
    )
    attr_row_frame, attr_row = make_row(spacing=8)
    attr_btn = QPushButton("Attr [customAttr=true] — click to toggle")
    attr_btn.setProperty("class", "attr-btn")
    attr_btn.setProperty("customAttr", False)
    attr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    attr_row.addWidget(attr_btn)
    attr_quoted_btn = QPushButton('Quoted [customAttr="true"] — click to toggle')
    attr_quoted_btn.setProperty("class", "attr-btn-quoted")
    attr_quoted_btn.setProperty("customAttr", False)
    attr_quoted_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    attr_row.addWidget(attr_quoted_btn)
    attr_body.addWidget(attr_row_frame)

    def _toggle_attr(btn: QPushButton) -> Callable[[bool], None]:
        def _go(_checked: bool = False) -> None:
            btn.setProperty("customAttr", not bool(btn.property("customAttr")))

        return _go

    attr_btn.clicked.connect(_toggle_attr(attr_btn))
    attr_quoted_btn.clicked.connect(_toggle_attr(attr_quoted_btn))
    attr_body.addWidget(make_hint("Click: setProperty flips customAttr — background eases green/gray."))

    spec_row_frame, spec_row = make_row(spacing=8)
    spec_btn = QPushButton("ID wins (.spec-btn + #spec-id)")
    spec_btn.setObjectName("spec-id")
    spec_btn.setProperty("class", "spec-btn")
    spec_row.addWidget(spec_btn)
    typeid_btn = QPushButton("Type#id (QPushButton#typeid-btn) — hover me")
    typeid_btn.setObjectName("typeid-btn")
    typeid_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    spec_row.addWidget(typeid_btn)
    attr_body.addWidget(spec_row_frame)
    attr_body.addWidget(
        make_hint("#spec-id stays blue even though .spec-btn comes later. The Type#id button only matches QPushButton + that name.")
    )
    content_layout.addWidget(attr_card)
    cards["attrs"] = attr_card

    # ------------------------------------------------- 10. playground
    play_card, play_body = make_card(
        "Playground",
        "Runtime widgets, visibility toggles and window focus. New widgets are picked up via Polish events.",
    )
    ctl_frame, ctl_row = make_row(spacing=8)
    add_btn = QPushButton("Add .btn")
    add_btn.setProperty("class", "btn")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    rm_btn = QPushButton("Remove last")
    rm_btn.setProperty("class", "btn")
    rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    hide_btn = QPushButton("Hide / show last")
    hide_btn.setProperty("class", "btn")
    hide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ctl_row.addWidget(add_btn)
    ctl_row.addWidget(rm_btn)
    ctl_row.addWidget(hide_btn)
    play_body.addWidget(ctl_frame)

    dyn_host = QFrame()
    dyn_host.setProperty("class", "dynamic-host")
    dyn_host_layout = QVBoxLayout(dyn_host)
    dyn_host_layout.setContentsMargins(12, 12, 12, 12)
    dyn_host_layout.setSpacing(8)
    dyn_placeholder = QLabel("Dynamic widgets land here…")
    dyn_placeholder.setProperty("class", "placeholder")
    dyn_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dyn_host_layout.addWidget(dyn_placeholder)
    play_body.addWidget(dyn_host)

    def _on_add() -> None:
        if dyn_placeholder.parentWidget() is not None and not dyn_placeholder.isHidden():
            dyn_placeholder.setHidden(True)
        add_widget(dyn_host)

    def _on_remove() -> None:
        remove_widget()
        if not dynamic_btns and dyn_placeholder.parentWidget() is not None:
            dyn_placeholder.setHidden(False)

    add_btn.clicked.connect(_on_add)
    rm_btn.clicked.connect(_on_remove)
    hide_btn.clicked.connect(hide_widget)

    active_frame, active_row = make_row(spacing=8)
    active_lbl = QLabel(":active — lit while this window is focused")
    active_lbl.setProperty("class", "active-demo")
    active_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    active_row.addWidget(active_lbl, stretch=1)
    popup_btn = QPushButton("Open popup")
    popup_btn.setProperty("class", "btn")
    popup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    active_row.addWidget(popup_btn)
    play_body.addWidget(active_frame)

    popups: list[QWidget] = []

    def _open_popup() -> None:
        popup = QWidget(window, Qt.WindowType.Dialog)
        popup.setWindowTitle("Popup — click the main window to restore :active")
        popup.resize(340, 90)
        popup_inner = QVBoxLayout(popup)
        popup_inner.addWidget(QLabel("Now click the main window — watch :active transition back."))
        popups.append(popup)

        def _on_destroyed() -> None:
            if popup in popups:
                popups.remove(popup)

        popup.destroyed.connect(_on_destroyed)
        popup.show()

    popup_btn.clicked.connect(_open_popup)
    play_body.addWidget(make_hint("Minimize or focus another app to see :active fade out."))
    content_layout.addWidget(play_card)
    cards["playground"] = play_card

    footer = QLabel("qt-css-engine — TransitionEngine installed as app event filter • static QSS in, animated props out")
    footer.setProperty("class", "footer")
    footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
    footer.setWordWrap(True)
    content_layout.addWidget(footer)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
